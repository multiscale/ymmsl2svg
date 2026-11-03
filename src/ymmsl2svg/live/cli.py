"""ymmsl2svg-live command line interface.

``ymmsl2svg-live open FILE``    serve on a loopback TCP port and open a browser.
"""

import logging
import os
import sys
from pathlib import Path

import click

from ymmsl2svg.settings import settings


def default_tcp_port() -> int:
    """Deterministic per-user loopback port for ``ymmsl2svg-live open``.

    Below the Linux ephemeral range (32768+) to avoid collisions with
    short-lived connections, and per-user so two people on the same node
    don't clash. NB a loopback TCP port is connectable by other users on the
    same node.
    """
    return 10000 + os.getuid() % 10000


def _setup_logging() -> None:
    # Our own INFO messages only: ymmsl logs every YAML node it parses at INFO
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("ymmsl2svg").setLevel(logging.INFO)


def _serve(**kwargs) -> None:
    from ymmsl2svg.live import server

    try:
        server.serve(**kwargs)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@click.group()
@click.version_option(package_name="ymmsl2svg")
def main() -> None:
    """Live viewer: re-render a yMMSL file in your browser on every save."""


@main.command("open")
@click.argument(
    "ymmsl_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--port",
    "tcp_port",
    type=int,
    default=None,
    help="Loopback TCP port to serve on. Default: a per-user port, "
    "10000 + uid % 10000.",
)
@click.option(
    "--open-browser/--no-open-browser",
    default=True,
    show_default=True,
    help="Open a browser at the URL once serving.",
)
@click.option(
    "--poll",
    is_flag=True,
    help="Only poll the file for changes, don't use inotify.",
)
@click.option("-d", "--debug", is_flag=True, help="Enable debug visualizations.")
def open_(
    ymmsl_file: Path,
    tcp_port: int | None,
    open_browser: bool,
    poll: bool,
    debug: bool,
) -> None:
    """Serve on a local port and open a browser (blocking).

    YMMSL_FILE is re-rendered every time it is saved.
    """
    _setup_logging()
    settings.debug = debug
    _serve(
        ymmsl_file=ymmsl_file,
        tcp_port=tcp_port or default_tcp_port(),
        open_browser=open_browser,
        poll=poll,
    )


if __name__ == "__main__":
    main()
