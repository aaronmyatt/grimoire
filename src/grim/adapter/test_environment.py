"""Tests for adapter/environment.py's GrimEnvironment — the tool-calling
enforcement point. No live model: execute() is called directly with
structured {tool, args} action dicts against a real tmp GRIM_DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

from minisweagent.exceptions import Submitted  # noqa: E402

from grim.adapter.environment import GrimEnvironment  # noqa: E402


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
    _write(env, "prints_sentinel", "bash", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
    run = env.execute(_tool("run", {"name": "prints_sentinel"}))
    assert run["returncode"] == 0  # ran fine, did NOT raise Submitted


def test_bad_arg_value_returns_error_observation_not_systemexit() -> None:
    # The model validates that required args are present, but a bad *value*
    # (a non-numeric timeout) still reaches argparse, which sys.exit()s.
    # _invoke traps that SystemExit so it returns as a normal nonzero
    # observation the agent can read and fix — never a process kill.
    env = GrimEnvironment(session_id="s1")
    _write(env, "greet", "bash", "echo hi")
    run = env.execute(_tool("run", {"name": "greet", "timeout": "notanumber"}))
    assert run["returncode"] != 0
    assert "usage:" in run["output"]
