import re
from dataclasses import asdict
from importlib.resources import files
from string import Template

import svg
from ymmsl.v0_2 import Model

from ymmsl2svg.model_block import ModelBlock
from ymmsl2svg.settings import settings


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
        model = ModelBlock(self.model)

        return svg.SVG(
            width=model.width,
            height=model.height,
            elements=[
                self._style(),
                self._defs(),
                model.to_svg(),
            ],
        )
