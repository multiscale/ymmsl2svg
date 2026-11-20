import heapq
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Component, Model, Operator, Port, Reference, Timeline
from ymmsl.v0_2.timeline_resolver import TimelineChecker

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.conduit_ducts import ConduitDuct, TopConduitDuct
from ymmsl2svg.port_groups import HorizontalPortGroup
from ymmsl2svg.timeline_layer import TimelineLayer

if TYPE_CHECKING:
    from ymmsl2svg.port_groups import PortBlock


class TimelineBlock(SvgBlock):
    """Container for everything inside a timeline.

    This component is responsible for the layout and ordering of all components inside a
    yMMSL timeline. Subtimelines are instantiated and layed out recursively.
    """

    def __init__(
        self,
        model: Model,
        timeline_checker: TimelineChecker,
        timeline: Timeline,
        layer: TimelineLayer,
        parent: ComponentBlock | None = None,
    ) -> None:
        super().__init__()

        self.timeline = timeline
        self.layer = layer
        layer.add(self)
        self.parent = parent

        self.transform: svg.Transform = svg.Translate(0, 0)

        components = self._ordered_components(model, timeline_checker)
        self.top_conduit_duct = TopConduitDuct(self, timeline)
        self.conduit_ducts: list[ConduitDuct] = [
            ConduitDuct(self.top_conduit_duct) for _ in range(len(components) + 1)
        ]

        self.port_group: HorizontalPortGroup | None = None
        if parent is not None:
            self.port_group = HorizontalPortGroup(parent, self)
            ports = self._ports_per_subtimeline(timeline_checker, parent.component)
            for port in ports[timeline]:
                self.port_group.add_port(port)

        # Create each component with the subtimelines that its O_I and S ports
        # send/receive in. The subtimelines must be attached before the next
        # component is created, as the conduit ducts number their connectors in
        # order of registration.
        self.components: list[ComponentBlock] = []
        for i, component in enumerate(components):
            cblock = ComponentBlock(
                component, layer, self.conduit_ducts[i], self.conduit_ducts[i + 1]
            )
            for subtimeline in self._ports_per_subtimeline(timeline_checker, component):
                cblock.add_subtimeline(
                    TimelineBlock(
                        model, timeline_checker, subtimeline, layer.next_layer(), cblock
                    )
                )
            assert len(cblock.ports) == len(component.ports)
            self.components.append(cblock)

    def _ordered_components(
        self, model: Model, timeline_checker: TimelineChecker
    ) -> list[Component]:
        """Components in this timeline, in drawing order (left to right).

        A component that sends to an F_INIT port of another component (or of a
        component in its subtimelines) is drawn to the left of it. Model order is
        used as tie breaker.
        """
        components = []
        for component in model.components.values():
            assert component.timeline is not None, "Component timelines are not set"
            if component.timeline.parent == self.timeline:
                components.append(component)
        index = {component: i for i, component in enumerate(components)}

        dependencies: set[tuple[int, int]] = set()
        for conduit in model.conduits:
            if not conduit.sending_component() or not conduit.receiving_component():
                continue  # Ignore model ports
            receiver = model.components[conduit.receiving_component()]
            if receiver.ports[conduit.receiving_port()].operator is not Operator.F_INIT:
                continue
            sender = model.components[conduit.sending_component()]
            # Both sender and receiver must be in our timeline, or a subtimeline
            sender_ancestor = self._ancestor(components, timeline_checker, sender)
            receiver_ancestor = self._ancestor(components, timeline_checker, receiver)
            if sender_ancestor is None or receiver_ancestor is None:
                continue
            if sender_ancestor is not receiver_ancestor:
                dependencies.add((index[sender_ancestor], index[receiver_ancestor]))

        order = _topological_sort(dependencies, len(components))
        return [components[i] for i in order]

    @staticmethod
    def _ports_per_subtimeline(
        timeline_checker: TimelineChecker, component: Component
    ) -> dict[Timeline, list[Port]]:
        """O_I and S ports of a component, by the subtimeline (relative to the model)
        that they send/receive in, in order of the ports."""
        result: dict[Timeline, list[Port]] = {}
        for port in component.ports.values():
            if port.operator in (Operator.O_I, Operator.S):
                timeline = timeline_checker.timeline_for_port(
                    component.name + port.name
                )
                result.setdefault(timeline, []).append(port)
        return result

    def _ancestor(
        self,
        components: list[Component],
        timeline_checker: TimelineChecker,
        component: Component,
    ) -> Component | None:
        """The component in this timeline that component is, or is nested in.

        Args:
            components: Components in this timeline.
            timeline_checker: ymmsl's checker of the model timelines.
            component: Component to get the ancestor of.

        Returns:
            None if component is not in this timeline or one of its subtimelines.
        """
        assert component.timeline is not None
        if component.timeline.parent == self.timeline:
            return component
        try:
            relative = component.timeline.parent.relative_to(self.timeline)
        except ValueError:  # component is not in a subtimeline
            return None
        subtimeline = self.timeline + relative[0]
        for candidate in components:
            ports = self._ports_per_subtimeline(timeline_checker, candidate)
            if subtimeline in ports:
                return candidate
        return None

    @property
    def subtimelines(self) -> list["TimelineBlock"]:
        """All direct subtimelines, of all components in this timeline."""
        return [subtl for c in self.components for subtl in c.subtimelines]

    @property
    def ports(self) -> list["PortBlock"]:
        """O_I and S ports of the parent component in this timeline."""
        if self.port_group is None:
            return []
        return self.port_group.o_i_ports + self.port_group.s_ports

    def _iter_cd_and_components(self) -> Iterator[ConduitDuct | ComponentBlock]:
        """Iterate over all ConduitDucts and ComponentBlocks (left to right)."""
        for i, component in enumerate(self.components):
            yield self.conduit_ducts[i]
            yield component
        yield self.conduit_ducts[-1]

    def map_components(self) -> dict[Reference, ComponentBlock]:
        """Recursively map all ComponentBlocks by their component name"""
        result = {c.component.name: c for c in self.components}
        for subtl in self.subtimelines:
            result.update(subtl.map_components())
        return result

    def get_component_sort_keys(self) -> dict[Reference, tuple[int, ...]]:
        """Recursively calculate the sort key of each component (for sorting ports)."""
        result = {}
        for i, component in enumerate(self.components):
            result[component.component.name] = (i,)
            for subtimeline in component.subtimelines:
                for compname, key in subtimeline.get_component_sort_keys().items():
                    result[compname] = (i,) + key
        return result

    def sort_output_ports(self, key: Callable) -> None:
        """Sort O_I/O_F ports in all components in this timeline and sub-timelines."""
        for component in self.components:
            component.sort_output_ports(key)
        for subtimeline in self.subtimelines:
            subtimeline.sort_output_ports(key)

    def sort_input_ports(self, key: Callable) -> None:
        """Sort F_INIT/S ports in all components in this timeline and sub-timelines."""
        for component in self.components:
            component.sort_input_ports(key)
        for subtimeline in self.subtimelines:
            subtimeline.sort_input_ports(key)

    def route_conduits(self) -> None:
        self.top_conduit_duct.route_conduits()

    def calc_layout(self) -> None:
        """Calculate the size and layout of the timeline block and its contents."""
        self.top_conduit_duct.calc_layout()
        self.top_conduit_duct.height = self.layer.conduitduct_height

        width = 0
        for item in self._iter_cd_and_components():
            item.x = width
            item.y = self.top_conduit_duct.height
            if isinstance(item, ComponentBlock):
                # Center the component rectangles in the timeline layer
                item.y += (
                    self.layer.component_height - item.minimum_component_height
                ) // 2
            item.calc_layout()
            width += item.width

        self.width = width
        self.top_conduit_duct.width = self.width
        self.height = self.layer.height + max(
            (tl.height for tl in self.subtimelines), default=0
        )

    def place(self, x: float, width: float, top: float) -> None:
        """Place this subtimeline below its parent component, and lay out the parent's
        O_I and S ports above it.

        Args:
            x: x-position of the segment of the parent component reserved for this
                subtimeline, in the frame of the parent's timeline.
            width: Width of that segment.
            top: y-position of the bottom of the parent component rectangle, in the
                frame of the parent's timeline.
        """
        assert self.parent is not None and self.port_group is not None
        # The subtimeline is aligned with its timeline layer, so it may be lower than
        # the bottom of the port group: the conduits then extend up to the ports.
        subtimeline_y = self.parent.layer.height
        self.moveto(x, subtimeline_y)
        self.port_group.x = 0
        self.port_group.y = top - subtimeline_y
        self.port_group.width = width
        self.port_group.calc_layout()

    def moveto(self, x: float, y: float) -> None:
        """Move the complete subtimeline by setting a translation filter on the
        containing SVG group."""
        self.transform = svg.Translate(x, y)

    def to_svg(self) -> svg.G:
        """Build the SVG representing this timeline."""
        group = super().to_svg()
        assert group.elements is not None
        # Add sub-elements
        if self.port_group is not None:
            group.elements.append(self.port_group.to_svg())
        group.elements.extend(item.to_svg() for item in self._iter_cd_and_components())
        group.elements.extend(tl.to_svg() for tl in self.subtimelines)
        group.elements.append(self.top_conduit_duct.to_svg())
        # Set translation
        group.transform = [self.transform]
        return group


def _topological_sort(dependencies: Iterable[tuple[int, int]], num: int) -> list[int]:
    """Sort nodes 0..num-1 topologically, use node number as tie breaker.

    Algorithm based on networkx.algorithms.dag.lexicographical_topological_sort, see
    https://networkx.org/documentation/stable/_modules/networkx/algorithms/dag.html#lexicographical_topological_sort
    """
    dependencies = list(dependencies)
    indegrees = [0] * num
    for _, to in dependencies:
        indegrees[to] += 1
    zero_indegree = [i for i in range(num) if indegrees[i] == 0]
    heapq.heapify(zero_indegree)

    order = []
    while zero_indegree:
        node = heapq.heappop(zero_indegree)
        order.append(node)
        for from_, to in dependencies:
            if from_ != node:
                continue
            indegrees[to] -= 1
            if indegrees[to] == 0:
                heapq.heappush(zero_indegree, to)

    if any(indegrees):
        raise ValueError("Graph contains a cycle")
    return order
