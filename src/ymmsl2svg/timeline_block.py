from collections.abc import Iterator

import svg
from ymmsl.v0_2 import Reference, TimelineNode, TimelineTree

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
        if len(node.parent_components) > 1:
            raise NotImplementedError(
                "Visualization for interact coupling is not yet implemented."
            )

        self.transform: svg.Transform = svg.Translate(0, 0)

        self.top_conduit_duct = TopConduitDuct(node.name)
        self.conduit_ducts: list[ConduitDuct] = [
            ConduitDuct(node.name, self.top_conduit_duct)
            for _ in range(len(node.components) + 1)
        ]
        self.components: list[ComponentBlock] = []

        # Subtimelines
        self.subtimelines: list[TimelineBlock] = []
        subtl_per_component: dict[Reference, list[TimelineBlock]] = {}
        for subnode in node.children:
            subtimeline = TimelineBlock(tree, subnode)
            self.subtimelines.append(subtimeline)
            for component in subnode.parent_components:
                subtl_per_component.setdefault(component.name, []).append(subtimeline)

        # For now we take the order of components from the timeline, but we should
        # revisit this:
        # - Support interact coupling (components with shared timelines must be next to
        #   each other)
        # - Minimize conduit crossings
        for i, component in enumerate(node.components):
            subtimelines = subtl_per_component.get(component.name, [])
            cblock = ComponentBlock(
                component,
                subtimelines,
                self.conduit_ducts[i],
                self.conduit_ducts[i + 1],
            )
            self.components.append(cblock)

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
        group.elements.append(self.top_conduit_duct.to_svg())
        group.elements.extend(item.to_svg() for item in self._iter_cd_and_components())
        group.elements.extend(tl.to_svg() for tl in self.subtimelines)
        # Set translation
        group.transform = [self.transform]
        return group
