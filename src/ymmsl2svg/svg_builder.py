import math
import re
from dataclasses import asdict
from importlib.resources import files
from string import Template

import svg
from ymmsl.v0_2 import Model

from ymmsl2svg.conduit_ducts import (
    assign_basename_colors,
    basename_color,
    port_basename,
)
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
        if not settings.draw_port_icons:
            return svg.Defs(elements=[])
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

    def _basenames(self) -> list[str]:
        """Distinct port basenames across all conduits, in stable (sorted) order."""
        names: list[str] = []
        for conduit in self.model.conduits:
            name = port_basename(conduit.sending_port())
            if name not in names:
                names.append(name)
        names.sort()
        return names

    def _legend(self, available_width: float) -> tuple[svg.G | None, float, float]:
        """Legend mapping each port basename to its conduit colour.

        Laid out in as many columns (filled top-to-bottom) as fit in
        ``available_width``, so a long list of signals stays compact. Returns the
        group (or None if there are no conduits) and its width/height.
        """
        basenames = self._basenames()
        if not basenames:
            return None, 0.0, 0.0
        row_h = 17.0
        swatch_w = 22.0
        text_gap = 7.0
        col_gap = 18.0
        pad = 4.0
        font_size = 13.0
        col_w = swatch_w + text_gap + max(len(s) for s in basenames) * 7.5 + col_gap
        n = len(basenames)
        ncols = max(1, min(n, int(available_width // col_w)))
        rows = math.ceil(n / ncols)
        elements: list[svg.Element] = []
        for j, name in enumerate(basenames):
            col, row = divmod(j, rows)  # column-major: fill each column top-down
            x = col * col_w
            y = pad + row * row_h + row_h / 2
            elements.append(
                svg.Line(
                    x1=x, y1=y, x2=x + swatch_w, y2=y,
                    stroke=basename_color(name), stroke_width=3,
                    stroke_linecap="square",
                )
            )
            elements.append(
                svg.Text(
                    text=name, x=x + swatch_w + text_gap, y=y,
                    dominant_baseline="central", font_size=font_size,
                )
            )
        width = ncols * col_w
        height = 2 * pad + rows * row_h
        return svg.G(class_=["legend"], elements=elements), width, height

    def build_svg(self) -> svg.SVG:
        """Build the SVG for the given yMMSL model."""
        model = ModelBlock(self.model)
        # Assign basename colours before rendering so conduits and legend agree.
        if settings.color_conduits:
            assign_basename_colors(self._basenames())
        elements: list[svg.Element] = [self._style(), self._defs(), model.to_svg()]

        # A legend (port basename -> colour) is placed below the model.
        legend, legend_w, legend_h = (
            self._legend(model.width) if settings.color_conduits else (None, 0.0, 0.0)
        )
        width = model.width
        height = model.height
        if legend is not None:
            gap = 12.0
            legend_x = 2 * settings.port_margin
            legend.transform = [svg.Translate(legend_x, model.height + gap)]
            elements.append(legend)
            width = max(width, legend_x + legend_w)
            height = model.height + gap + legend_h

        return svg.SVG(width=width, height=height, elements=elements)
