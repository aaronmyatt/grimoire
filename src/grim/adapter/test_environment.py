"""Tests for adapter/environment.py's GrimEnvironment — the tool-calling
enforcement point. No live model: execute() is called directly with
structured {tool, args} action dicts against a real tmp GRIM_DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

from minisweagent.exceptions import Submitted  # noqa: E402

from grim.adapter import trace  # noqa: E402
from grim.adapter.environment import GrimEnvironment  # noqa: E402

_FAIL_LOUD_EXIT = 3


@pytest.fixture(autouse=True)
def _grim_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))


def _tool(tool: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {"tool": tool, "args": args, "tool_call_id": call_id}


def _write(env: GrimEnvironment, name: str, lang: str, body: str) -> dict[str, Any]:
    args = {"name": name, "lang": lang, "desc": f"{name} test script", "body": body}
    return env.execute(_tool("write", args))


def test_write_then_run_round_trips_through_the_db() -> None:
    env = GrimEnvironment(session_id="s1")
    written = _write(env, "greet", "python", "print('hi')")
    assert written["returncode"] == 0
    assert "wrote greet@1" in written["output"]

    run = env.execute(_tool("run", {"name": "greet"}))
    assert run["returncode"] == 0
    assert "hi" in run["output"]


def test_two_runs_share_session_and_increment_seq() -> None:
    env = GrimEnvironment(session_id="fixed-session")
    _write(env, "greet", "python", "print('hi')")
    first = env.execute(_tool("run", {"name": "greet"}))
    second = env.execute(_tool("run", {"name": "greet"}))

    first_id = first["output"].split("exec #")[1].split(" ")[0]
    second_id = second["output"].split("exec #")[1].split(" ")[0]
    assert first_id != second_id


def test_run_actions_execute_from_the_pinned_launch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrong-directory fix: the launch cwd is captured at construction
    and every action executes from it, even after the process cwd drifts.
    The export is scoped to the call — GRIM_CWD never leaks between turns."""
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    env = GrimEnvironment(session_id="s1")
    monkeypatch.chdir(tmp_path)  # the process cwd drifts after startup...

    _write(env, "where", "python", "import os; print(os.getcwd())")
    run = env.execute(_tool("run", {"name": "where"}))

    assert run["returncode"] == 0
    assert str(launch.resolve()) in run["output"]  # ...but scripts still run from launch
    assert "GRIM_CWD" not in os.environ


def test_submit_tool_raises_submitted_with_the_result() -> None:
    # The deterministic stop: no output scanning — the result flows straight
    # into the submission, even though "submit" is not a library verb.
    env = GrimEnvironment(session_id="s1")
    with pytest.raises(Submitted) as exc:
        env.execute(_tool("submit", {"result": "aaronmyatt has 118 repos"}))
    assert exc.value.messages[0]["extra"]["submission"] == "aaronmyatt has 118 repos"


def test_running_a_sentinel_printing_script_does_not_submit() -> None:
    # Finishing is ONLY the submit tool now — a script that happens to print
    # the old sentinel must run like any other, never end the task. The whole
    # class of false-submission bugs is gone with the output-scan.
    env = GrimEnvironment(session_id="s1")
    _write(env, "prints_sentinel", "python", "print('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')")
    run = env.execute(_tool("run", {"name": "prints_sentinel"}))
    assert run["returncode"] == 0  # ran fine, did NOT raise Submitted


def test_database_error_returns_error_observation_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (2026-08-15): a `grim run shell` whose captured stdout blew
    # past SQLite's value-length limit raised sqlite3.DataError ("string or
    # blob too big") out of the in-process INSERT. _invoke only caught
    # OperationalError, so the DataError tore through mini's run loop and
    # killed a 255-step session (trajectory unsaved). Any sqlite3.Error must
    # degrade to a nonzero observation the agent can read and route around.
    env = GrimEnvironment(session_id="s1")

    def explode(argv: list[str]) -> int:
        raise sqlite3.DataError("string or blob too big")

    monkeypatch.setattr("grim.adapter.environment.cli.main", explode)
    run = env.execute(_tool("run", {"name": "anything"}))
    assert run["returncode"] == 1
    assert "string or blob too big" in run["output"]
    assert "DataError" in run["output"]


def test_bad_arg_value_returns_error_observation_not_systemexit() -> None:
    # The model validates that required args are present, but a bad *value*
    # (a non-numeric timeout) still reaches argparse, which sys.exit()s.
    # _invoke traps that SystemExit so it returns as a normal nonzero
    # observation the agent can read and fix — never a process kill.
    env = GrimEnvironment(session_id="s1")
    _write(env, "greet", "python", "print('hi')")
    run = env.execute(_tool("run", {"name": "greet", "timeout": "notanumber"}))
    assert run["returncode"] != 0
    assert "usage:" in run["output"]


def test_stamped_session_metadata_lands_on_the_session_row(tmp_path: Path) -> None:
    env = GrimEnvironment(session_id="stamped")
    env.stamp_session(" some/model ", "  build the thing  ")
    _write(env, "greet", "python", "print('hi')")

    conn = sqlite3.connect(tmp_path / "grimoire.db")
    row = conn.execute("SELECT model, task FROM session WHERE id = 'stamped'").fetchone()
    conn.close()
    assert row == ("some/model", "build the thing")
    assert "GRIM_SESSION_MODEL" not in os.environ  # exported per call, then restored


def test_unstamped_session_leaves_metadata_null(tmp_path: Path) -> None:
    env = GrimEnvironment(session_id="bare")
    _write(env, "greet", "python", "print('hi')")

    conn = sqlite3.connect(tmp_path / "grimoire.db")
    row = conn.execute("SELECT model, task FROM session WHERE id = 'bare'").fetchone()
    conn.close()
    assert row == (None, None)


def test_tool_span_records_the_observation_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Instrumentation is auto-off under pytest and the verdict is cached in
    # trace's globals, so force it on and reset that cache for this test only.
    log = tmp_path / "instrumentation.jsonl"
    monkeypatch.setenv("GRIM_INSTRUMENT", "1")
    monkeypatch.setenv("GRIM_INSTRUMENT_FILE", str(log))
    monkeypatch.setattr(trace, "_disabled", False)
    monkeypatch.setattr(trace, "_handle", None)
    env = GrimEnvironment(session_id="s1")
    _write(env, "fail_loud", "python", "import sys\nsys.exit(3)")
    env.execute(_tool("run", {"name": "fail_loud"}))
    trace._flush()

    spans = [json.loads(line) for line in log.read_text().splitlines()]
    by_phase = {span["phase"]: span for span in spans if span.get("kind") == "span"}
    assert by_phase["tool.write"]["returncode"] == 0
    assert by_phase["tool.run"]["returncode"] == _FAIL_LOUD_EXIT


_BUDGET_S = "900"
_LEFT_S = 100.0


def test_no_time_budget_leaves_run_args_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_TIME_BUDGET_S", raising=False)
    env = GrimEnvironment(session_id="s1")
    args = {"name": "greet", "timeout": 900}
    assert env.fit_run_to_budget(args) is args


def test_run_timeout_is_cut_to_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_TIME_BUDGET_S", _BUDGET_S)
    env = GrimEnvironment(session_id="s1")
    env.started_s -= float(_BUDGET_S) - _LEFT_S  # pretend 800s have passed
    fitted = env.fit_run_to_budget({"name": "greet", "timeout": 900})
    assert fitted["name"] == "greet"
    assert _LEFT_S - 5 < fitted["timeout"] <= _LEFT_S
    unset = env.fit_run_to_budget({"name": "greet"})
    assert unset["timeout"] <= _LEFT_S  # the 120s default no longer fits either


def _edit(env: GrimEnvironment, old: str, new: str) -> dict[str, Any]:
    args = {"name": "greet", "changelog": "edit", "old": old, "new": new}
    return env.execute(_tool("update", args))


def test_update_old_new_appends_a_version_with_one_replacement() -> None:
    env = GrimEnvironment(session_id="s1")
    _write(env, "greet", "python", "print('hi')\nprint('bye')\n")
    edited = _edit(env, "print('hi')", "print('hello')")
    assert edited["returncode"] == 0
    run = env.execute(_tool("run", {"name": "greet"}))
    assert "hello" in run["output"]
    assert "bye" in run["output"]


def test_update_old_new_reports_a_missing_or_ambiguous_snippet() -> None:
    env = GrimEnvironment(session_id="s1")
    _write(env, "greet", "python", "print('x')\nprint('x')\n")
    missing = _edit(env, "nope", "x")
    assert missing["returncode"] == 1
    assert "not found" in missing["output"]
    twice = _edit(env, "print('x')", "print('y')")
    assert twice["returncode"] == 1
    assert "matches 2 places" in twice["output"]


def test_update_old_new_on_an_unknown_script_is_an_observation() -> None:
    env = GrimEnvironment(session_id="s1")
    result = _edit(env, "a", "b")
    assert result["returncode"] == 1
    assert "no script by that name" in result["output"]
