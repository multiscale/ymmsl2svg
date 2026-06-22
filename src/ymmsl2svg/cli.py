import logging
import sys
from pathlib import Path
from typing import TextIO

import click

from ymmsl2svg import ymmsl2svg
from ymmsl2svg.settings import settings

_path_type = click.Path(exists=True, path_type=Path)


@click.command(no_args_is_help=True)
@click.argument("ymmsl_files", nargs=-1, required=True, type=_path_type)
@click.option("-o", "--output", type=click.File("w"), help="Output file name.")
@click.option("-d", "--debug", default=False, help="Enable debug visualizations.")
@click.option(
    "--port-icons/--no-port-icons",
    default=True,
    help="Draw port icons. Use --no-port-icons to omit them and collapse the spacing "
    "they reserve (ports attach directly to the component edge).",
)
@click.version_option()
def main(
    ymmsl_files: list[Path], output: TextIO | None, debug: bool, port_icons: bool
):
    """Generate an SVG visualization for the provided yMMSL.

    By default the generated SVG is printed to the console output. Use the -o/--output
    option to specify an output file. The output file will be overwritten if it already
    exists!

    \b
    Arguments:
      YMMSL_FILES:  The yMMSL file containing the model you want to visualize.
    """
    logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    if len(ymmsl_files) > 1:
        raise NotImplementedError(
            "Stacking multiple yMMSL files is not yet implemented"
        )

    settings.debug = debug
    if not port_icons:
        settings.disable_port_icons()
    svg = ymmsl2svg(ymmsl_files[0])

    if output is None:
        output = sys.stdout
    output.write(str(svg))
    output.write("\n")
