from collections.abc import Callable, Iterator

import svg
from ymmsl.v0_2 import Reference

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.conduit_ducts import ConduitDuct, TopConduitDuct
from ymmsl2svg.timeline_node import TimelineNode


class TimelineBlock(SvgBlock):
    """Container for everything inside a timeline.

    This component is responsible for the layout and ordering of all components inside a
    yMMSL timeline. Subtimelines are instantiated and layed out recursively.
    """

    def __init__(self, node: TimelineNode) -> None:
        super().__init__()

        self.node = node
        if len(node.parent_components) > 1:
            raise NotImplementedError(
                "Visualization for interact coupling is not yet implemented."
            )

        self.transform: svg.Transform = svg.Translate(0, 0)

        self.top_conduit_duct = TopConduitDuct(self, node.timeline)
        self.conduit_ducts: list[ConduitDuct] = [
            ConduitDuct(self.top_conduit_duct) for _ in range(len(node.components) + 1)
        ]
        self.components: list[ComponentBlock] = []

        # Subtimelines
        self.subtimelines: list[TimelineBlock] = []
        subtl_per_component: dict[Reference, list[TimelineBlock]] = {}
        for subnode in node.children.values():
            subtimeline = TimelineBlock(subnode)
            self.subtimelines.append(subtimeline)
            for component in subnode.parent_components:
                subtl_per_component.setdefault(component.name, []).append(subtimeline)

        # Order of components is determined by TimelineNode
        for i, component in enumerate(node.components):
            subtimelines = subtl_per_component.get(component.name, [])
            cblock = ComponentBlock(
                component,
                subtimelines,
                self.conduit_ducts[i],
                self.conduit_ducts[i + 1],
            )
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

    def calc_layout(self):
        """Calculate the size and layout of the timeline block and its contents."""
        for subtl in self.subtimelines:
            subtl.calc_layout()
        self.top_conduit_duct.calc_layout()

        width = 0
        height = 0
        for item in self._iter_cd_and_components():
            item.x = width
            item.y = self.top_conduit_duct.height
            item.calc_layout()
            width += item.width
            height = max(height, item.height)

        self.width = width
        self.top_conduit_duct.width = self.width
        self.height = (
            self.top_conduit_duct.height
            + max((c.height for c in self.components), default=0)
            + max((tl.height for tl in self.subtimelines), default=0)
        )

    def moveto(self, x: float, y: float) -> None:
        """Move the complete subtimeline by setting a translation filter on the
        containing SVG group."""
        self.transform = svg.Translate(x, y)

    def to_svg(self) -> svg.G:
        """Build the SVG representing this timeline."""
        group = super().to_svg()
        assert group.elements is not None
        # Add sub-elements
        group.elements.extend(item.to_svg() for item in self._iter_cd_and_components())
        group.elements.extend(tl.to_svg() for tl in self.subtimelines)
        group.elements.append(self.top_conduit_duct.to_svg())
        # Set translation
        group.transform = [self.transform]
        return group
