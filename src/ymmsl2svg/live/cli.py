"""ymmsl2svg-live command line interface."""

import logging
from pathlib import Path

import click

from ymmsl2svg.live import server
from ymmsl2svg.settings import settings


@click.command()
@click.argument(
    "ymmsl_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--port", default=8765, show_default=True, help="Local port to serve on.")
@click.option(
    "--open-browser/--no-open-browser",
    default=True,
    show_default=True,
    help="Open a browser at the URL once serving.",
)
@click.option("-d", "--debug", is_flag=True, help="Enable debug visualizations.")
@click.version_option(package_name="ymmsl2svg")
def main(ymmsl_file: Path, port: int, open_browser: bool, debug: bool) -> None:
    """Show YMMSL_FILE in your browser and re-render it on every save.

    Runs until interrupted with Ctrl-C.
    """
    # Our own INFO messages only: ymmsl logs every YAML node it parses at INFO
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("ymmsl2svg").setLevel(logging.INFO)
    settings.debug = debug
    try:
        server.serve(ymmsl_file, port, open_browser)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
