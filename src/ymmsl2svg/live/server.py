"""Live viewer server: re-renders a yMMSL file on every save.

Serves two routes:

* ``/``: the viewer page.
* ``/events``: a Server-Sent Events stream with the rendered SVG (or the
  error) of the watched file, pushed on every change.

The server listens on a loopback TCP port, so only this machine can connect.
"""

import json
import logging
import queue
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

    server: "_TCPServer"

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

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


class _TCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, viewer: Viewer) -> None:
        self.viewer = viewer
        super().__init__(("127.0.0.1", port), _Handler)


def make_server(viewer: Viewer, tcp_port: int) -> _TCPServer:
    """Create (but don't start) the server on a loopback TCP port."""
    try:
        return _TCPServer(tcp_port, viewer)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot serve on port {tcp_port}: {exc.strerror}. "
            "Pick another one with --port."
        ) from exc


def serve(ymmsl_file: Path, tcp_port: int, open_browser: bool, poll: bool) -> None:
    """Run the live viewer server (blocking).

    Args:
        ymmsl_file: The yMMSL file to watch and render.
        tcp_port: Loopback TCP port to serve on.
        open_browser: Open a browser at the URL once serving.
        poll: Only poll the file for changes, don't use inotify.
    """
    viewer = Viewer(ymmsl_file, poll=poll)
    server = make_server(viewer, tcp_port)
    viewer.start()
    logger.info("Serving at http://localhost:%d/", tcp_port)
    if open_browser:
        webbrowser.open(f"http://localhost:{tcp_port}/")

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        viewer.stop()
        server.shutdown()
        server.server_close()
