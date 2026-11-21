"""The jobs line (delegation plan 4c). Every test injects the tmux runner."""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

from grim.adapter import environment, launcher  # noqa: E402
from grim.adapter.environment import GrimEnvironment, jobs_line, tmux_jobs  # noqa: E402

_NOW_S = 1_800_000_000
_TWO_JOBS = "web\tjob\t{web}\t0\t\t\nask-x-claude\tagent\t{ask}\t1\t0\t1:900:ab\n"
_SHOWN_MAX, _LINE_MAX_CHARS = 8, 300
_TWO_SHOWN = "jobs: web job live 1m · ask-x-claude agent DONE 120m"


@pytest.fixture(autouse=True)
def _jobs_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.delenv(environment.JOBS_ENV)  # src/conftest.py turns it off for the suite


def _observe(listing: str) -> tuple[str, int]:
    env, calls, now = GrimEnvironment(session_id="jobs"), [0], int(time.time())

    def runner() -> str:
        calls[0] += 1
        return listing.format(web=now - 90, ask=now - 7200)

    env.jobs_runner = runner
    return env.execute({"tool": "list", "args": {}})["output"], calls[0]


def test_the_line_is_present_with_two_jobs() -> None:
    output, calls = _observe(_TWO_JOBS)
    assert output.splitlines()[-1] == _TWO_SHOWN
    assert calls == 1, "one tmux call per observation"


def test_the_line_is_absent_with_no_jobs() -> None:
    output, calls = _observe("")
    assert "jobs:" not in output and calls == 1
    assert jobs_line("\tjob\t1\t0\t\t\n", _NOW_S) == "", "an untagged window is not grim's"


def test_the_line_is_capped_with_twelve_jobs() -> None:
    rows = "".join(f"job-{n:02}\tjob\t{_NOW_S}\t1\t7\t\n" for n in range(12))
    line = jobs_line(rows, _NOW_S)
    assert line.count(" dead:7 0m") == _SHOWN_MAX and line.endswith(" · +4 more")
    assert len(line) <= _LINE_MAX_CHARS


def test_a_tmux_error_yields_no_line_and_no_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_TMUX_SOCKET", f"grimtest-none-{tmp_path.name}")
    assert tmux_jobs() == "", "no server: tmux exits non-zero, and nothing raises"
    monkeypatch.setenv("PATH", str(tmp_path))  # no tmux at all
    assert tmux_jobs() == "", "no tmux: OSError is caught"


def test_no_jobs_strips_the_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import minisweagent.run.mini as minirun  # noqa: PLC0415 -- the optional extra

    forwarded: list[list[str]] = []
    monkeypatch.setattr(minirun, "app", lambda args, standalone_mode: forwarded.append(args))
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # non-tty -> stays in place
    assert launcher.main(["--no-jobs", "a task", "-m", "x/y"]) == 0
    assert "--no-jobs" not in forwarded[0]
    output, calls = _observe(_TWO_JOBS)
    assert "jobs:" not in output and calls == 0, "off means tmux is not even asked"
