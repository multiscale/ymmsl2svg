from collections.abc import Callable, Iterator

import svg
from ymmsl.v0_2 import Reference

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.conduit_ducts import ConduitDuct, TopConduitDuct
from ymmsl2svg.port_groups import HorizontalPortGroup
from ymmsl2svg.timeline_layer import TimelineLayer
from ymmsl2svg.timeline_tree import TimelineTree


class TimelineBlock(SvgBlock):
    """Container for everything inside a timeline.

    This component is responsible for the layout and ordering of all components inside a
    yMMSL timeline. Subtimelines are instantiated and layed out recursively.
    """

    def __init__(self, tree: TimelineTree, layer: TimelineLayer) -> None:
        super().__init__()

        self.tree = tree
        if len(tree.parent_components) > 1:
            raise NotImplementedError(
                "Visualization for interact coupling is not yet implemented."
            )
        self.layer = layer
        layer.add(self)

        self.parent: ComponentBlock | None = None
        """Parent component of this timeline (set by ComponentBlock.add_subtimeline),
        None for the root timeline."""
        self.offset: tuple[float, float] = (0, 0)
        """Position of this timeline in the frame of its parent's timeline (see
        moveto)."""

        self.top_conduit_duct = TopConduitDuct(self, tree.timeline)
        self.conduit_ducts: list[ConduitDuct] = [
            ConduitDuct(self.top_conduit_duct) for _ in range(len(tree.components) + 1)
        ]
        self.components: list[ComponentBlock] = []

        self.port_group: HorizontalPortGroup | None = None
        """O_I and S ports of the parent component in this timeline (set by
        ComponentBlock.add_subtimeline), None for the root timeline."""

        # Subtimelines
        self.subtimelines: list[TimelineBlock] = []
        for subtree in tree.children.values():
            subtimeline = TimelineBlock(subtree, layer.next_layer())
            self.subtimelines.append(subtimeline)

        # Order of components is determined by TimelineTree. Each component is
        # created with the subtimelines that its O_I and S ports send/receive in.
        # The subtimelines must be attached before the next component is created, as
        # the conduit ducts number their connectors in order of registration.
        for i, component in enumerate(tree.components):
            cblock = ComponentBlock(
                component,
                layer,
                self.conduit_ducts[i],
                self.conduit_ducts[i + 1],
            )
            for subtimeline in self.subtimelines:
                ports = subtimeline.tree.parent_ports.get(component)
                if ports:
                    cblock.add_subtimeline(subtimeline, ports)
            assert len(cblock.ports) == len(component.ports)
            self.components.append(cblock)

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
        self.offset = (x, y)

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
        group.transform = [svg.Translate(*self.offset)]
        return group
