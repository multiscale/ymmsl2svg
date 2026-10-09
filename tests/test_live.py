import http.client
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from ymmsl2svg.live.server import Viewer, _claim_socket, make_server
from ymmsl2svg.live.watch import POLL_INTERVAL_SECONDS, FileWatcher, filesystem_type

configurations = Path(__file__).parent / "configurations"
on_linux = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="inotify is Linux-only"
)


@pytest.fixture
def ymmsl_file(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.ymmsl"
    shutil.copy(configurations / "simple-dispatch.ymmsl", path)
    return path


def _touch(path: Path) -> None:
    """Change the file's content (and thereby its signature)."""
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


# Each variant watches with a long backstop, so only the mechanism under test
# can report changes within the timeout.
# variant: (poll, backstop_interval, timeout)
WATCHERS = {
    "inotify": (False, 60.0, 1.0),
    "poll": (True, 60.0, POLL_INTERVAL_SECONDS * 5),
}


def _edit_in_place(path: Path) -> None:
    _touch(path)


def _replace_by_rename(path: Path) -> None:
    # How Vim, Emacs and VS Code often save: write a temp file, rename it over
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(path.read_text() + "\n")
    os.replace(tmp, path)


def _delete_and_recreate(path: Path) -> None:
    content = path.read_text()
    path.unlink()
    time.sleep(0.3)
    path.write_text(content + "\n")


@pytest.mark.parametrize(
    "variant",
    [pytest.param("inotify", marks=on_linux), "poll"],
)
@pytest.mark.parametrize(
    "edit", [_edit_in_place, _replace_by_rename, _delete_and_recreate]
)
def test_watcher_detects_change(ymmsl_file: Path, variant: str, edit) -> None:
    poll, backstop_interval, timeout = WATCHERS[variant]
    changes = _Changes()
    watcher = FileWatcher(
        ymmsl_file, changes, poll=poll, backstop_interval=backstop_interval
    )
    watcher.start()
    try:
        assert watcher.method.startswith(variant[:4])  # "inot" / "poll"
        edit(ymmsl_file)
        assert changes.wait_for(1, timeout)
    finally:
        watcher.stop()


@on_linux
def test_watcher_reports_one_save_once(ymmsl_file: Path) -> None:
    changes = _Changes()
    watcher = FileWatcher(ymmsl_file, changes, backstop_interval=0.05)
    watcher.start()
    try:
        _replace_by_rename(ymmsl_file)
        assert changes.wait_for(1, 1.0)
        time.sleep(0.5)  # inotify and several backstop polls have run
        assert changes.count == 1
    finally:
        watcher.stop()


def test_watcher_ignores_other_files(ymmsl_file: Path) -> None:
    changes = _Changes()
    watcher = FileWatcher(ymmsl_file, changes, backstop_interval=0.05)
    watcher.start()
    try:
        (ymmsl_file.parent / "other.ymmsl").write_text("x")
        assert not changes.wait_for(1, 0.5)
    finally:
        watcher.stop()


def test_filesystem_type(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "/dev/sdd / ext4 rw 0 0\n"
        "C:\\134 /mnt/c 9p rw 0 0\n"
        "server:/home /mnt/my\\040share nfs4 rw 0 0\n"
    )
    assert filesystem_type(Path("/home/user/x.ymmsl"), mounts) == "ext4"
    assert filesystem_type(Path("/mnt/c/Users/x.ymmsl"), mounts) == "9p"
    assert filesystem_type(Path("/mnt/my share/x.ymmsl"), mounts) == "nfs4"
    assert filesystem_type(Path("/x"), tmp_path / "missing") is None


@on_linux
def test_remote_filesystem_polls_fast(ymmsl_file: Path, tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(f"/dev/sdd / ext4 rw 0 0\nC: {ymmsl_file.parent} 9p rw 0 0\n")
    changes = _Changes()
    watcher = FileWatcher(ymmsl_file, changes, mounts=mounts, backstop_interval=60)
    watcher.start()
    try:
        assert watcher.method.startswith("inotify + polling every 0.3 s")
        assert "9p" in watcher.method
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


def test_claim_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "test.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as live:
        live.bind(str(socket_path))
        live.listen()
        with pytest.raises(RuntimeError, match="already serving"):
            _claim_socket(socket_path)
    # Closed without unlinking: a stale socket file is left behind
    assert socket_path.exists()
    _claim_socket(socket_path)
    assert not socket_path.exists()


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path) -> None:
        super().__init__("localhost", timeout=5)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.socket_path))


def _read_event(response: http.client.HTTPResponse) -> dict:
    while True:
        line = response.fp.readline().decode()
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])


@pytest.fixture
def running_server(ymmsl_file: Path, tmp_path: Path, request):
    """Start a viewer + server; yields a function creating a connection."""
    viewer = Viewer(ymmsl_file)
    if request.param == "tcp":
        server = make_server(viewer, tcp_port=0)
        port = server.socket.getsockname()[1]

        def connect():
            return http.client.HTTPConnection("127.0.0.1", port, timeout=5)

    else:
        socket_path = tmp_path / "viewer.sock"
        server = make_server(viewer, socket_path=socket_path)
        assert socket_path.stat().st_mode & 0o777 == 0o600

        def connect():
            return _UnixHTTPConnection(socket_path)

    viewer.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield connect
    viewer.stop()
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("running_server", ["tcp", "unix"], indirect=True)
def test_server(running_server, ymmsl_file: Path) -> None:
    conn = running_server()
    conn.request("GET", "/")
    response = conn.getresponse()
    assert response.status == 200
    assert b"EventSource" in response.read()

    conn = running_server()
    conn.request("GET", "/events")
    response = conn.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"
    first = _read_event(response)
    assert first["ok"]
    assert first["svg"].startswith("<svg")

    _touch(ymmsl_file)
    second = _read_event(response)
    assert second["ok"]
    assert second["updated"] >= first["updated"]


@pytest.mark.parametrize("running_server", ["tcp"], indirect=True)
def test_server_rejects_foreign_host(running_server) -> None:
    conn = running_server()
    conn.request("GET", "/", headers={"Host": "evil.example:1234"})
    assert conn.getresponse().status == 403
