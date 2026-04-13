import re

import svg
from ymmsl.v0_2 import Model, TimelineTree

from ymmsl2svg.timeline_block import TimelineBlock


class SVGBuilder:
    """Class responsible for building an SVG image from a yMMSL v0.2 Model."""

    def __init__(self, model: Model) -> None:
        self.model = model

    def _style(self) -> svg.Style:
        """Build CSS style sheet for the SVG image."""
        style = """
            .TimelineBlock {
                fill: rgb(195, 130, 255);
            }
            .TopConduitDuct {
                fill: #ccc;
            }
            .ComponentBlock {
                fill: rgb(134, 146, 255);
            }
        """
        # Remove whitespace to reduce file size
        style = re.sub(r"\s+", "", style)
        return svg.Style(text=style)

    def _defs(self) -> svg.Defs:
        """Reusable graphic components (e.g. port visualizations)."""
        return svg.Defs()

    def build_svg(self) -> svg.SVG:
        """Build the SVG for the given yMMSL model."""
        # Determine timelines
        timeline_tree = TimelineTree(self.model)
        timeline_block = TimelineBlock(timeline_tree, timeline_tree.root)

        return svg.SVG(
            width=timeline_block.width,
            height=timeline_block.height,
            elements=[
                self._style(),
                self._defs(),
                timeline_block.to_svg(),
            ],
        )
