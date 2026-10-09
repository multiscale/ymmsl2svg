"""Smoke tests for the ymmsl2svg-live CLI surface."""

import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from ymmsl2svg.live import cli
from ymmsl2svg.settings import settings

ymmsl_file = str(Path(__file__).parent / "configurations" / "simple-dispatch.ymmsl")


@pytest.fixture(autouse=True)
def restore_settings(monkeypatch):
    # The commands set the global settings.debug; restore it after each test.
    monkeypatch.setattr(settings, "debug", settings.debug)


def test_all_commands_registered():
    assert set(cli.main.commands) == {"socket", "open"}


def test_socket_has_no_tcp_options():
    # TCP and the browser live on `ymmsl2svg-live open`, not `socket`.
    result = CliRunner().invoke(cli.main, ["socket", ymmsl_file, "--port", "5006"])
    assert result.exit_code != 0


def test_open_reports_port_in_use():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        result = CliRunner().invoke(
            cli.main, ["open", ymmsl_file, "--port", str(port), "--no-open-browser"]
        )
    # A clean one-line error, not a traceback
    assert result.exit_code == 1
    assert f"Error: Cannot serve on port {port}" in result.output


def test_socket_reports_live_server(tmp_path):
    socket_path = tmp_path / "live.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as live:
        live.bind(str(socket_path))
        live.listen()
        result = CliRunner().invoke(
            cli.main, ["socket", ymmsl_file, "--socket", str(socket_path)]
        )
    assert result.exit_code == 1
    assert f"Error: ymmsl2svg-live is already serving on {socket_path}" in (
        result.output
    )
