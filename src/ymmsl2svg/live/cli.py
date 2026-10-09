"""ymmsl2svg-live command line interface.

  ``ymmsl2svg-live socket FILE``  serve on a unix socket (for an SSH forward).
  ``ymmsl2svg-live open FILE``    serve on a loopback TCP port and open a browser.

Reach the socket from your machine with an SSH LocalForward, e.g.::

    LocalForward 127.0.0.1:4334 /home/%r/.ymmsl2svg.sock
"""

import logging
import os
import sys
from pathlib import Path

import click

from ymmsl2svg.settings import settings

# In the home dir so that one ssh_config line works for every user:
#   LocalForward 127.0.0.1:4334 /home/%r/.ymmsl2svg.sock
# (%r expands to the remote username). Unix sockets are host-local even
# on a shared filesystem: pin one login node and run ymmsl2svg-live there.
DEFAULT_SOCKET = Path("~/.ymmsl2svg.sock").expanduser()


def default_tcp_port() -> int:
    """Deterministic per-user loopback port for ``ymmsl2svg-live open``.

    Below the Linux ephemeral range (32768+) to avoid collisions with
    short-lived connections, and per-user so two people on the same node
    don't clash. NB unlike the 0600 socket, a loopback TCP port is
    connectable by other users on the same node.
    """
    return 10000 + os.getuid() % 10000


DEFAULT_LOCAL_PORT = 4334

#: Positional yMMSL file, shared by every command.
ymmsl_file_argument = click.argument(
    "ymmsl_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
poll_option = click.option(
    "--poll",
    is_flag=True,
    help="Only poll the file for changes, don't use inotify.",
)
debug_option = click.option(
    "-d", "--debug", is_flag=True, help="Enable debug visualizations."
)


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


@main.command("socket")
@ymmsl_file_argument
@click.option(
    "--socket",
    "socket_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_SOCKET,
    show_default=True,
    help="Unix socket to serve on.",
)
@click.option(
    "--local-port",
    default=DEFAULT_LOCAL_PORT,
    show_default=True,
    help="Local port the SSH forward uses; logged as the URL to open.",
)
@poll_option
@debug_option
def socket_cmd(
    ymmsl_file: Path, socket_path: Path, local_port: int, poll: bool, debug: bool
) -> None:
    """For remote access: serve on a unix socket via your SSH forward (blocking).

    Reached through an SSH LocalForward to the socket, in ~/.ssh/config on
    your own machine (%r expands to your remote username):

    \b
        LocalForward 127.0.0.1:4334 /home/%r/.ymmsl2svg.sock

    and then http://localhost:4334. YMMSL_FILE is re-rendered every time it
    is saved.
    """
    _setup_logging()
    settings.debug = debug
    _serve(
        ymmsl_file=ymmsl_file,
        socket_path=socket_path,
        local_port=local_port,
        poll=poll,
    )


@main.command("open")
@ymmsl_file_argument
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
@poll_option
@debug_option
def open_(
    ymmsl_file: Path,
    tcp_port: int | None,
    open_browser: bool,
    poll: bool,
    debug: bool,
) -> None:
    """On this machine: serve on a local port and open a browser (blocking).

    For a desktop session running on the machine itself (e.g. WSL or
    NoMachine), where no SSH socket forward is involved. A loopback TCP port
    is also the fallback where sshd forbids unix-socket forwarding: run
    ``ymmsl2svg-live open --no-open-browser`` and forward the port with a
    plain ``LocalForward 127.0.0.1:<port> 127.0.0.1:<port>``. YMMSL_FILE is
    re-rendered every time it is saved.
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
