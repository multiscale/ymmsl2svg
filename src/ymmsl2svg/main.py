from pathlib import Path

import svg
import ymmsl
from ymmsl.v0_2 import Configuration, Reference, resolve

from ymmsl2svg.svg_builder import SVGBuilder


def ymmsl2svg(ymmsl_source: str | Path) -> svg.SVG:
    """Visualize a yMMSL graph as an SVG image.

    Args:
        ymmsl_source: A string containing the yMMSL data of your simulation, or
            a Path to a file containing yMMSL data.

    Returns:
        Scalable Vector Graphics image.
    """
    configuration = ymmsl.load_as(Configuration, ymmsl_source)
    return configuration2svg(configuration)


def configuration2svg(configuration: Configuration) -> svg.SVG:
    """Visualize a yMMSL graph as an SVG image.

    Args:
        configuration: A yMMSL v0.2 configuration.

    Returns:
        Scalable Vector Graphics image.
    """
    # Ensure we have a valid configuration
    resolve(Reference([]), configuration)
    configuration.check_consistent()

    # Assume a single model for now:
    if len(configuration.models) == 0:
        raise RuntimeError("The provided configuration doesn't contain any models.")
    elif len(configuration.models) > 1:
        raise NotImplementedError(
            "ymmsl2svg doesn't support multiple models in one configuration yet."
        )

    model = configuration.root_model()
    builder = SVGBuilder(model)
    return builder.build_svg()
