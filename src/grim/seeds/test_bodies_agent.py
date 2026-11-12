"""Runtime tests for the delegate seeds in seeds/bodies_agent.py (feature 4,
docs/features/04-delegates.md), dispatched exactly as `grim run` would. The
delegate is a fake TUI (a raw-mode script that takes bracketed pastes), wired
in through [delegates.AGENT] bin in a test config file, on a private tmux
server. No real agent is started and no model is called."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 30.0
_USAGE_EXIT_CODE = 2


def _run(name: str, argv: list[str], stdin: str | None = None) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=stdin, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", name)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "work").mkdir()
    yield name
    _tmux(name, "kill-server")
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


@pytest.mark.parametrize(
    "case",
    [
        (["claude", "job"], "brief", "usage: agent_start AGENT JOB DIR"),
        (["claude", "job", "{work}", "--dangerously-skip-permissions"], "brief", "usage: agent_"),
        (["gemini", "job", "{work}"], "brief", "unknown agent 'gemini'"),
        (["claude", "bad name", "{work}"], "brief", "bad job name"),
        (["claude", "job", "{work}/missing"], "brief", "not a directory"),
        (["claude", "job", "{work}"], "  ", "the brief goes on stdin"),
    ],
    ids=["no-dir", "extra-flag", "unknown-agent", "bad-name", "no-such-dir", "no-brief"],
)
def test_agent_start_wrong_calls_exit_2_and_start_nothing(
    socket: str, tmp_path: Path, case: tuple[list[str], str, str]
) -> None:
    argv, stdin, said = case
    result = _run("agent_start", [a.format(work=tmp_path / "work") for a in argv], stdin=stdin)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert said in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0
