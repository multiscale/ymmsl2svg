"""Live viewer server: re-renders a yMMSL file on every save.

Serves two routes:

* ``/``: the viewer page.
* ``/events``: a Server-Sent Events stream with the rendered SVG (or the
  error) of the watched file, pushed on every change.

The server listens on a per-user unix socket (``~/.ymmsl2svg.sock``, mode
0600, which on a shared login node restricts access to the owning user) and
is reached through an SSH forward, e.g. in ``~/.ssh/config``::

    LocalForward 127.0.0.1:4334 /home/%r/.ymmsl2svg.sock

(``%r`` expands to the remote username, so one line serves every user.)
Alternatively it serves on a loopback TCP port, for use on the machine itself.
"""

import json
import logging
import os
import queue
import signal
import socket
import socketserver
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

from ymmsl2svg.live.watch import FileWatcher
from ymmsl2svg.main import ymmsl2svg

logger = logging.getLogger(__name__)

#: Comment sent on idle event streams, so closed browser tabs are noticed.
KEEPALIVE_SECONDS = 15

#: Host names the viewer may be addressed as. Anything else is refused, so a
#: web page can't reach the viewer through DNS rebinding.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class _WarningCollector(logging.Handler):
    """Collects warnings logged while rendering, to show them in the viewer."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class Viewer:
    """Renders the watched yMMSL file and pushes the result to browsers."""

    def __init__(self, ymmsl_file: Path, poll: bool = False) -> None:
        self.ymmsl_file = ymmsl_file.absolute()
        self.watcher = FileWatcher(self.ymmsl_file, self.render, poll=poll)
        self.state: dict[str, Any] = {}
        self._last_svg: str | None = None
        self._render_lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._subscribers_lock = threading.Lock()

    def start(self) -> None:
        """Start watching and render the file's current state."""
        self.watcher.start()
        self.render()

    def stop(self) -> None:
        """Stop watching and end all event streams."""
        self.watcher.stop()
        self._publish(None)

    def render(self) -> None:
        """Render the file and push the result (or the error) to all browsers."""
        with self._render_lock:
            collector = _WarningCollector()
            package_logger = logging.getLogger("ymmsl2svg")
            package_logger.addHandler(collector)
            try:
                svg = str(ymmsl2svg(self.ymmsl_file))
                error = None
            except Exception as exc:
                svg = None
                error = f"{type(exc).__name__}: {exc}"
                logger.info("Rendering %s failed: %s", self.ymmsl_file, error)
            finally:
                package_logger.removeHandler(collector)

            if svg is not None:
                self._last_svg = svg
            self.state = {
                "ok": error is None,
                # On errors: the last good diagram, so the view doesn't go blank
                "svg": self._last_svg,
                "error": error,
                "warnings": collector.messages,
                "file": str(self.ymmsl_file),
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            self._publish(self.state)

    def subscribe(self) -> queue.Queue:
        """Queue receiving the current state, then each update (None: stop)."""
        q: queue.Queue = queue.Queue()
        with self._subscribers_lock:
            self._subscribers.append(q)
            q.put(self.state)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._subscribers_lock:
            self._subscribers.remove(q)

    def _publish(self, state: dict[str, Any] | None) -> None:
        with self._subscribers_lock:
            for q in self._subscribers:
                q.put(state)


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for the viewer page and its event stream."""

    server: "_TCPServer | _UnixServer"

    def do_GET(self) -> None:
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in _ALLOWED_HOSTS:
            self.send_error(403, "Use http://localhost:<port>/")
        elif self.path == "/":
            self._send_page()
        elif self.path == "/events":
            self._send_events()
        else:
            self.send_error(404)

    def _send_page(self) -> None:
        body = (files("ymmsl2svg.live") / "viewer.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        viewer = self.server.viewer
        updates = viewer.subscribe()
        try:
            while True:
                try:
                    state = updates.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    if state is None:
                        return
                    self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser tab closed
        finally:
            viewer.unsubscribe(updates)

    def address_string(self) -> str:
        # Unix socket peers have no (host, port) address
        if isinstance(self.client_address, tuple):
            return str(self.client_address[0])
        return "unix-socket"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


class _TCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, viewer: Viewer) -> None:
        self.viewer = viewer
        super().__init__(("127.0.0.1", port), _Handler)


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: Path, viewer: Viewer) -> None:
        self.viewer = viewer
        self.socket_path = socket_path
        super().__init__(str(socket_path), _Handler)

    def server_bind(self) -> None:
        super().server_bind()
        os.chmod(self.socket_path, 0o600)


def _socket_alive(socket_path: Path) -> bool:
    """True if something is already accepting connections on the socket."""
    if not socket_path.exists():
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.connect(str(socket_path))
            return True
        except OSError:
            return False


def _claim_socket(socket_path: Path) -> None:
    """Remove a stale socket, or fail if a live server owns it."""
    if _socket_alive(socket_path):
        raise RuntimeError(f"ymmsl2svg-live is already serving on {socket_path}")
    socket_path.unlink(missing_ok=True)


def make_server(
    viewer: Viewer, socket_path: Path | None = None, tcp_port: int | None = None
) -> socketserver.TCPServer:
    """Create (but don't start) the server, on a unix socket or a TCP port."""
    if socket_path is not None:
        _claim_socket(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _UnixServer(socket_path, viewer)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot serve on unix socket {socket_path}: {exc}. "
                "Pick another one with --socket (at most ~100 characters)."
            ) from exc
    assert tcp_port is not None
    try:
        return _TCPServer(tcp_port, viewer)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot serve on port {tcp_port}: {exc.strerror}. "
            "Pick another one with --port."
        ) from exc


def serve(
    ymmsl_file: Path,
    socket_path: Path | None = None,
    local_port: int | None = None,
    tcp_port: int | None = None,
    open_browser: bool = False,
    poll: bool = False,
) -> None:
    """Run the live viewer server (blocking).

    Args:
        ymmsl_file: The yMMSL file to watch and render.
        socket_path: Unix socket to serve on (mode 0600). Either this or
            tcp_port must be given.
        local_port: Port the SSH forward uses; logged as the URL the socket
            is reachable at. Socket-only.
        tcp_port: Loopback TCP port to serve on, when not serving on a socket.
        open_browser: Open a browser at the TCP URL once serving (for a
            desktop session running on the machine itself). TCP-only.
        poll: Only poll the file for changes, don't use inotify.
    """
    viewer = Viewer(ymmsl_file, poll=poll)
    server = make_server(viewer, socket_path, tcp_port)
    viewer.start()

    if socket_path is not None:
        logger.info("Serving on unix socket %s", socket_path)
        if local_port:
            logger.info(
                "→ reachable at http://localhost:%d/ (via your SSH LocalForward)",
                local_port,
            )
    else:
        logger.info("Serving at http://localhost:%d/", tcp_port)
        if open_browser:
            webbrowser.open(f"http://localhost:{tcp_port}/")

    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Also shut down cleanly (removing the socket) on kill or SSH disconnect
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, signal.default_int_handler)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        viewer.stop()
        server.shutdown()
        server.server_close()
        if socket_path is not None:
            socket_path.unlink(missing_ok=True)
