import re
from dataclasses import asdict
from importlib.resources import files
from string import Template

import svg
from ymmsl.v0_2 import Model, TimelineTree

from ymmsl2svg.settings import settings
from ymmsl2svg.timeline_block import TimelineBlock


def _get_style() -> str:
    template = (files("ymmsl2svg") / "style.template.css").read_text()
    style = Template(template).substitute(asdict(settings))
    # Remove comments and whitespace to reduce SVG file size
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    style = re.sub(r"\s+", " ", style)
    style = re.sub(r"\s*([{{}}:;,>+~\(\)\[\]=])\s*", r"\1", style)
    return style


class SVGBuilder:
    """Class responsible for building an SVG image from a yMMSL v0.2 Model."""

    def __init__(self, model: Model) -> None:
        self.model = model

    def _style(self) -> svg.Style:
        """Build CSS style sheet for the SVG image."""
        return svg.Style(text=_get_style())

    def _defs(self) -> svg.Defs:
        """Reusable graphic components (e.g. port visualizations)."""
        f_init = svg.Path(
            id="port-f_init",
            d=[svg.M(1, 0), svg.L(4, -3), svg.L(7, 0), svg.L(4, 3), svg.Z()],
        )
        o_f = svg.Path(
            id="port-o_f",
            d=[svg.M(-1, 0), svg.L(-4, -3), svg.L(-7, 0), svg.L(-4, 3), svg.Z()],
        )
        o_i = svg.Circle(id="port-o_i", r=2.5, cy=-3.5)
        s = svg.Circle(id="port-s", r=2.5, cy=-3.5)
        return svg.Defs(elements=[f_init, o_f, o_i, s])

    def build_svg(self) -> svg.SVG:
        """Build the SVG for the given yMMSL model."""
        # Determine timelines
        timeline_tree = TimelineTree(self.model)
        timeline_tree.check_consistent()
        timeline_block = TimelineBlock(timeline_tree, timeline_tree.root)

        # Map all components
        components = timeline_block.map_components()
        # Route conduits
        for conduit in self.model.conduits:
            destination_timeline = timeline_tree.timeline_for_port(conduit.receiver)
            sending_component = conduit.sending_component()
            if len(sending_component) == 0:  # Conduit sender is a model port
                tcd = timeline_block.top_conduit_duct
                tcd.route_from_left(conduit, destination_timeline.name)
            else:
                sender = components[sending_component]
                sender.route_conduit(conduit, destination_timeline.name)

        timeline_block.calc_layout()

        return svg.SVG(
            width=timeline_block.width,
            height=timeline_block.height,
            elements=[
                self._style(),
                self._defs(),
                timeline_block.to_svg(),
            ],
        )
