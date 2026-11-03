"""Live viewer server: re-renders a yMMSL file on every save.

Serves two routes on a loopback TCP port, so only this machine can connect:

* ``/``: the viewer page.
* ``/events``: a Server-Sent Events stream with the rendered SVG (or the
  error) of the watched file, pushed on every change.

Changes are detected with watchfiles, which uses the operating system's file
notifications and decides by itself when to poll instead (e.g. on WSL). The
file's parent directory is watched rather than the file itself, so saves that
replace the file (writing a temporary file and renaming it over the original,
as Vim, Emacs and VS Code do) are seen too.
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

import watchfiles

from ymmsl2svg.main import ymmsl2svg

logger = logging.getLogger(__name__)

#: Comment sent on idle event streams, so closed browser tabs are noticed.
KEEPALIVE_SECONDS = 15

#: Host names the viewer may be addressed as. Anything else is refused, so a
#: web page can't reach the viewer through DNS rebinding.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class Viewer:
    """Renders the yMMSL file on every save and pushes the result to browsers."""

    def __init__(self, ymmsl_file: Path) -> None:
        self.ymmsl_file = ymmsl_file.absolute()
        self.state: dict[str, Any] = {}
        self._last_svg: str | None = None
        self._stop = threading.Event()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._subscribers: list[queue.Queue] = []
        self._subscribers_lock = threading.Lock()

    def start(self) -> None:
        """Render the file's current state, then re-render on every change."""
        self.render()
        logger.info("Watching %s", self.ymmsl_file)
        self._watcher.start()

    def stop(self) -> None:
        """Stop watching and end all event streams."""
        self._stop.set()
        self._watcher.join()
        self._publish(None)

    def _watch(self) -> None:
        # Changes from one save are grouped, so this renders once per save
        for _ in watchfiles.watch(
            self.ymmsl_file.parent,
            watch_filter=lambda _, path: Path(path).name == self.ymmsl_file.name,
            stop_event=self._stop,
            recursive=False,
        ):
            self.render()

    def render(self) -> None:
        """Render the file and push the result (or the error) to all browsers."""
        try:
            self._last_svg = str(ymmsl2svg(self.ymmsl_file))
            error = None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.info("Rendering %s failed: %s", self.ymmsl_file, error)
        self.state = {
            "ok": error is None,
            # On errors: the last good diagram, so the view doesn't go blank
            "svg": self._last_svg,
            "error": error,
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

    server: "_Server"

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


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, viewer: Viewer) -> None:
        self.viewer = viewer
        super().__init__(("127.0.0.1", port), _Handler)


def make_server(viewer: Viewer, port: int) -> _Server:
    """Create (but don't start) the server on a loopback TCP port."""
    try:
        return _Server(port, viewer)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot serve on port {port}: {exc.strerror}. "
            "Pick another one with --port."
        ) from exc


def serve(ymmsl_file: Path, port: int, open_browser: bool) -> None:
    """Run the live viewer server until interrupted with Ctrl-C.

    Args:
        ymmsl_file: The yMMSL file to watch and render.
        port: Loopback TCP port to serve on.
        open_browser: Open a browser at the URL once serving.
    """
    viewer = Viewer(ymmsl_file)
    server = make_server(viewer, port)
    viewer.start()
    url = f"http://localhost:{port}/"
    logger.info("Serving at %s", url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        viewer.stop()
        server.server_close()
