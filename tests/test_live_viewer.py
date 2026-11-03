"""Tests for the live viewer: watching and rendering, server and CLI."""

import http.client
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from ymmsl2svg.live import cli
from ymmsl2svg.live.server import Viewer, make_server
from ymmsl2svg.settings import settings

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


def _replace_by_rename(path: Path) -> None:
    # How Vim, Emacs and VS Code often save: write a temp file, rename it over
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(path.read_text() + "\n")
    os.replace(tmp, path)


@pytest.fixture
def viewer(ymmsl_file: Path):
    """A started viewer; yields a queue with its rendered states."""
    viewer = Viewer(ymmsl_file)
    viewer.start()
    time.sleep(1.0)  # watchfiles sets up its watch (or first poll) in the background
    updates = viewer.subscribe()
    updates.get()  # The initial render
    yield updates
    viewer.stop()


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(_touch, id="in-place"),
        pytest.param(_replace_by_rename, id="rename"),
    ],
)
def test_viewer_rerenders_on_save(viewer, ymmsl_file: Path, edit) -> None:
    edit(ymmsl_file)
    assert viewer.get(timeout=2.0)["ok"]


def test_viewer_rerenders_one_save_once(viewer, ymmsl_file: Path) -> None:
    (ymmsl_file.parent / "other.ymmsl").write_text("x")  # Must be ignored
    _replace_by_rename(ymmsl_file)
    viewer.get(timeout=2.0)
    time.sleep(0.5)
    assert viewer.empty()


def test_viewer_render(ymmsl_file: Path) -> None:
    viewer = Viewer(ymmsl_file)
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
    server = make_server(viewer, port=0)
    port = server.socket.getsockname()[1]
    viewer.start()
    time.sleep(1.0)  # watchfiles sets up its watch (or first poll) in the background
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


def test_cli_reports_port_in_use(ymmsl_file: Path, monkeypatch) -> None:
    # The command sets the global settings.debug; restore it afterwards
    monkeypatch.setattr(settings, "debug", settings.debug)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        result = CliRunner().invoke(
            cli.main,
            [str(ymmsl_file), "--port", str(port), "--no-open-browser"],
        )
    # A clean one-line error, not a traceback
    assert result.exit_code == 1
    assert f"Error: Cannot serve on port {port}" in result.output
