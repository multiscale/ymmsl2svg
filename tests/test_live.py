import http.client
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from ymmsl2svg.live.server import Viewer, make_server
from ymmsl2svg.live.watch import FileWatcher

configurations = Path(__file__).parent / "configurations"


@pytest.fixture
def ymmsl_file(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.ymmsl"
    shutil.copy(configurations / "simple-dispatch.ymmsl", path)
    return path


def _touch(path: Path) -> None:
    """Change the file's content."""
    with path.open("a") as f:
        f.write("\n")


class _Changes:
    """Collects watcher callbacks so tests can wait for them."""

    def __init__(self) -> None:
        self.count = 0
        self._cond = threading.Condition()

    def __call__(self) -> None:
        with self._cond:
            self.count += 1
            self._cond.notify_all()

    def wait_for(self, count: int, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self.count >= count, timeout)


def _replace_by_rename(path: Path) -> None:
    # How Vim, Emacs and VS Code often save: write a temp file, rename it over
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(path.read_text() + "\n")
    os.replace(tmp, path)


def _start_watcher(path: Path, changes: _Changes, poll: bool = False) -> FileWatcher:
    watcher = FileWatcher(path, changes, poll=poll)
    watcher.start()
    time.sleep(0.3)  # watchfiles sets up its watch in the background
    return watcher


@pytest.mark.parametrize(
    "poll, edit",
    [
        pytest.param(False, _touch, id="in-place"),
        pytest.param(False, _replace_by_rename, id="rename"),
        pytest.param(True, _touch, id="poll"),
    ],
)
def test_watcher_detects_change(ymmsl_file: Path, poll: bool, edit) -> None:
    changes = _Changes()
    watcher = _start_watcher(ymmsl_file, changes, poll)
    try:
        edit(ymmsl_file)
        assert changes.wait_for(1, 2.0)
    finally:
        watcher.stop()


def test_watcher_reports_one_save_once(ymmsl_file: Path) -> None:
    changes = _Changes()
    watcher = _start_watcher(ymmsl_file, changes)
    try:
        (ymmsl_file.parent / "other.ymmsl").write_text("x")  # Must be ignored
        _replace_by_rename(ymmsl_file)
        assert changes.wait_for(1, 2.0)
        time.sleep(0.5)
        assert changes.count == 1
    finally:
        watcher.stop()


def test_viewer_render(ymmsl_file: Path) -> None:
    viewer = Viewer(ymmsl_file, poll=True)
    viewer.render()
    assert viewer.state["ok"]
    good_svg = viewer.state["svg"]
    assert good_svg.startswith("<svg")

    ymmsl_file.write_text("ymmsl_version: v0.2\nmodels: [unclosed\n")
    viewer.render()
    assert not viewer.state["ok"]
    assert viewer.state["error"]
    assert viewer.state["svg"] == good_svg  # Last good diagram is kept


def _read_event(response: http.client.HTTPResponse) -> dict:
    while True:
        line = response.fp.readline().decode()
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])


@pytest.fixture
def connect(ymmsl_file: Path):
    """Start a viewer + server; yields a function creating a connection."""
    viewer = Viewer(ymmsl_file)
    server = make_server(viewer, tcp_port=0)
    port = server.socket.getsockname()[1]
    viewer.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    viewer.stop()
    server.shutdown()
    server.server_close()


def test_server(connect, ymmsl_file: Path) -> None:
    conn = connect()
    conn.request("GET", "/")
    response = conn.getresponse()
    assert response.status == 200
    assert b"EventSource" in response.read()

    conn = connect()
    conn.request("GET", "/events")
    response = conn.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"
    first = _read_event(response)
    assert first["ok"]
    assert first["svg"].startswith("<svg")

    _touch(ymmsl_file)
    assert _read_event(response)["ok"]  # An update arrives after the save


def test_server_rejects_foreign_host(connect) -> None:
    conn = connect()
    conn.request("GET", "/", headers={"Host": "evil.example:1234"})
    assert conn.getresponse().status == 403
