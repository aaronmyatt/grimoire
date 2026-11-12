"""Runtime tests for the tmux job seeds in seeds/bodies_tmux.py (feature 1,
docs/features/01-jobs.md), dispatched exactly as `grim run` would. Every test
drives a private tmux server on its own socket ($GRIM_TMUX_SOCKET), so the
developer's own tmux is never touched; the fixture kills that server."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 20.0
_USAGE_EXIT_CODE = 2
_TAKEN_EXIT_CODE = 1
_SLEEPER = ["sleep", "600"]  # outlives the test; the fixture's kill-server ends it
_WINDOW = "#{window_name} #{@grim_kind} #{@grim_job} #{?pane_dead,dead,alive}"


def _run(name: str, argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    # tmp_path is unique per test and per pytest run, so sockets never collide.
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", name)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    yield name
    # kill-server leaves the socket file behind; remove it so runs leave no litter.
    socket_path = _tmux(name, "display-message", "-p", "#{socket_path}").stdout.strip()
    _tmux(name, "kill-server")
    if socket_path:
        Path(socket_path).unlink(missing_ok=True)


def test_tmux_start_opens_a_tagged_window_and_returns_at_once(socket: str, tmp_path: Path) -> None:
    result = _run("tmux_start", ["api", *_SLEEPER])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"started grim:api\nlog: {tmp_path / 'run' / 'api.log'}\n"
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", _WINDOW).stdout == (
        "api job api alive\n"
    )


def test_tmux_start_splits_a_single_command_string(socket: str) -> None:
    assert _run("tmux_start", ["api", "sleep 600"]).exit_code == 0
    started = _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{pane_start_command}").stdout
    assert started.split() == _SLEEPER


def test_tmux_start_refuses_a_taken_job_name(socket: str) -> None:
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0
    again = _run("tmux_start", ["api", *_SLEEPER])
    assert again.exit_code == _TAKEN_EXIT_CODE
    assert "job 'api' already exists (live)" in again.stderr
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{window_name}").stdout == "api\n"


@pytest.mark.parametrize(
    "command",
    [["echo hi | cat"], ["echo", "hi", "&&", "ls"], ["sh", "-c", "echo hi"]],
    ids=["pipe", "and-chain", "sh-c"],
)
def test_tmux_start_refuses_shell_lines_and_starts_nothing(socket: str, command: list[str]) -> None:
    result = _run("tmux_start", ["api", *command])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "shell lines are refused" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0


@pytest.mark.parametrize(
    "argv", [[], ["api"], ["bad name", "sleep", "600"]], ids=["none", "no-program", "bad-name"]
)
def test_tmux_start_wrong_calls_exit_2_and_start_nothing(socket: str, argv: list[str]) -> None:
    result = _run("tmux_start", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "tmux_start" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0
