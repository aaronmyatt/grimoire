"""Runtime tests for agent_wait in seeds/bodies_delegate.py (docs/delegation-plan.md,
Phase 3), dispatched as `grim run` would, on a private tmux server. A delegate
finishing is acted out with the real agent_done body; no agent or model runs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS
from grim.seeds.bodies_delegate import AGENT_WAIT

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 30.0
_USAGE_EXIT_CODE, _BAD_END_EXIT_CODE, _TIMED_OUT_EXIT_CODE = 2, 3, 124
_MAX_POLLS = 2000  # bounded busy-waits on pgrep; each call is the pause
_PROMPT_S = 5.0  # "at once": far under any wait the tests ask for
_ANSWER = (  # an answer agent_done passes: the five headings and a numbered claim
    "## Verdict\nThe answer is 42.\n\n## Answer\n"
    + "It was computed, then checked. " * 4
    + "\n\n## Claims\n1. 6 x 7 = 42 — evidence: `python -c 'print(6*7)'` → 42\n\n"
    "## Confidence\nhigh — arithmetic\n\n## Open\nnothing\n"
)


def _run(name: str, argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _waiting(socket: str, *jobs: str) -> int:
    """How many tmux wait-for clients are blocked on these jobs right now."""
    found = [
        subprocess.run(["pgrep", "-f", f"tmux -L {socket} wait-for grim-{job}"], check=False)
        for job in jobs
    ]
    return sum(done.returncode == 0 for done in found)


def _waited(result: ExecutionResult) -> float:
    return float(result.stdout.splitlines()[-1].removeprefix("waited ").removesuffix("s"))


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", name)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "agent_done.py").write_text(next(s.body for s in SEEDS if s.name == "agent_done"))
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, check=False)
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


def _delegate(job: str) -> None:
    """A live agent-kind job, as agent_ask opens one."""
    assert _run("tmux_start", ["--kind", "agent", job, "sleep", "600"]).exit_code == 0


def _finish(tmp_path: Path, job: str) -> None:
    """The delegate writes its answer and agent_done checks, stamps and signals."""
    (tmp_path / "run" / job).mkdir(parents=True, exist_ok=True)
    (tmp_path / "run" / job / "result.md").write_text(_ANSWER)
    done = [sys.executable, str(tmp_path / "agent_done.py"), job]
    assert subprocess.run(done, capture_output=True, check=False).returncode == 0


def test_agent_wait_returns_as_soon_as_both_delegates_are_done(socket: str, tmp_path: Path) -> None:
    jobs = ("q-claude", "q-codex")
    for job in jobs:
        _delegate(job)
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(_run, "agent_wait", ["30", *jobs])
        assert any(_waiting(socket, *jobs) == len(jobs) for _ in range(_MAX_POLLS))
        _finish(tmp_path, "q-claude")
        _finish(tmp_path, "q-codex")
        result = blocked.result(timeout=_TIMEOUT_S)
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("q-claude DONE ") and lines[1].startswith("q-codex DONE ")
    assert _waited(result) < _PROMPT_S


@pytest.mark.parametrize("wake", ["left", "taken"])
def test_agent_wait_on_a_delegate_already_done_returns_at_once(
    socket: str, tmp_path: Path, wake: str
) -> None:
    _delegate("q-claude")
    _finish(tmp_path, "q-claude")
    if wake == "taken":  # someone else consumed the wake: status.json still decides
        subprocess.run(["tmux", "-L", socket, "wait-for", "grim-q-claude"], timeout=_TIMEOUT_S)
    result = _run("agent_wait", ["30", "q-claude"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("q-claude DONE ")
    assert _waited(result) == 0.0


def test_agent_wait_times_out_with_124_and_leaves_no_wait_for_client(socket: str) -> None:
    for job in ("q-claude", "q-codex"):
        _delegate(job)
    result = _run("agent_wait", ["1", "q-claude", "q-codex"])
    assert result.exit_code == _TIMED_OUT_EXIT_CODE
    assert result.stdout.startswith("q-claude RUNNING\nq-codex RUNNING\nwaited 1.")
    assert _waiting(socket, "q-claude", "q-codex") == 0


def test_agent_wait_never_sleeps() -> None:
    assert "time.sleep" not in AGENT_WAIT
    assert "selectors" in AGENT_WAIT  # it blocks on the wait-for clients instead


def test_agent_wait_on_a_delegate_stopped_mid_wait_exits_3(socket: str) -> None:
    for job in ("q-claude", "keep"):  # "keep" holds the server up after q-claude is gone
        _delegate(job)
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(_run, "agent_wait", ["30", "q-claude"])
        assert any(_waiting(socket, "q-claude") == 1 for _ in range(_MAX_POLLS))
        assert _run("tmux_stop", ["q-claude"]).exit_code == 0
        result = blocked.result(timeout=_TIMEOUT_S)
    assert result.exit_code == _BAD_END_EXIT_CODE
    assert result.stdout.startswith("q-claude STOPPED\n")


@pytest.mark.parametrize("case", [(0, 0), (7, _BAD_END_EXIT_CODE)], ids=["exit-0", "exit-7"])
def test_agent_wait_on_a_plain_job_follows_its_exit_code(
    socket: str, case: tuple[int, int]
) -> None:
    code, want = case
    started = _run("tmux_start", ["build", sys.executable, "-c", f"raise SystemExit({code})"])
    assert started.exit_code == 0
    result = _run("agent_wait", ["30", "build"])
    assert result.exit_code == want
    assert result.stdout.splitlines()[0] == ("build DONE" if code == 0 else "build FAILED exit 7")


def test_agent_wait_names_a_headless_failure_and_exits_3(socket: str, tmp_path: Path) -> None:
    _delegate("q-claude")
    _finish(tmp_path, "q-claude")
    (tmp_path / "run" / "q-codex").mkdir(parents=True)
    failed = {"job": "q-codex", "state": "AUTH", "stamped_at": 1, "agent": "codex", "exit_code": 1}
    (tmp_path / "run" / "q-codex" / "status.json").write_text(json.dumps(failed))
    meta = {"wall_s": 75.0, "cost_usd": None}
    (tmp_path / "run" / "q-codex" / "meta.json").write_text(json.dumps(meta))
    result = _run("agent_wait", ["30", "q-claude", "q-codex"])
    assert result.exit_code == _BAD_END_EXIT_CODE
    assert result.stdout.splitlines()[1] == "q-codex AUTH 1m15s cost=null exit 1"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["30"],
        ["0", "a"],
        ["3501", "a"],
        ["x", "a"],
        ["30", "a", "b", "c", "d", "e"],
        ["30", "a b"],
    ],
)
def test_agent_wait_wrong_calls_exit_2(socket: str, argv: list[str]) -> None:
    result = _run("agent_wait", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "agent_wait" in result.stderr
