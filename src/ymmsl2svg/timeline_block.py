from collections.abc import Iterator

import svg
from ymmsl.v0_2 import TimelineNode, TimelineTree

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.conduit_ducts import ConduitDuct, TopConduitDuct


class TimelineBlock(SvgBlock):
    """Container for everything inside a timeline.

    This component is responsible for the layout and ordering of all components inside a
    yMMSL timeline. Subtimelines are instantiated and layed out recursively.
    """

    def __init__(self, tree: TimelineTree, node: TimelineNode) -> None:
        super().__init__()
        self.tree = tree
        self.node = node

        self.top_conduit_duct = TopConduitDuct()
        self.conduit_ducts: list[ConduitDuct] = [ConduitDuct()]
        self.components: list[ComponentBlock] = []

        # For now we take the order of components from the timeline, but we should
        # revisit this:
        # - Support interact coupling (components with shared timelines must be next to
        #   each other)
        # - Minimize conduit crossings
        for component in node._components:
            self.components.append(ComponentBlock(component))
            self.conduit_ducts.append(ConduitDuct())

        self.calc_layout()

    def _iter_cd_and_components(
        self,
    ) -> Iterator[ConduitDuct | ComponentBlock]:
        """Iterate over all ConduitDucts and ComponentBlocks (left to right)."""
        for i, component in enumerate(self.components):
            yield self.conduit_ducts[i]
            yield component
        yield self.conduit_ducts[-1]

    def calc_layout(self):
        """Calculate the size and layout of the timeline block and its contents."""
        width = 0
        height = 0
        for item in self._iter_cd_and_components():
            item.x = width
            item.y = self.top_conduit_duct.height
            width += item.width
            height = max(height, item.height)

        self.width = width
        self.top_conduit_duct.width = self.width
        self.height = (
            max(c.height for c in self.components) + self.top_conduit_duct.height
        )

    def to_svg(self) -> svg.Element:
        """Build the SVG representing this timeline."""
        svg = super().to_svg()
        assert svg.elements is not None
        # Add sub-elements
        svg.elements.append(self.top_conduit_duct.to_svg())
        svg.elements.extend(item.to_svg() for item in self._iter_cd_and_components())
        return svg
