"""Tests for adapter/environment.py's GrimEnvironment — the six-verb
enforcement point. No live model: execute() is called directly with
scripted action dicts against a real tmp GRIM_DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from minisweagent.exceptions import Submitted

from grim.adapter.environment import PROTOCOL_REMINDER, GrimEnvironment


@pytest.fixture(autouse=True)
def _grim_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))


def _write_action(name: str, lang: str, desc: str, body: str) -> dict[str, str]:
    return {
        "command": f"grim write --name {name} --lang {lang} --desc \"{desc}\" <<'EOF'\n{body}\nEOF"
    }


def test_non_grim_command_returns_reminder_not_output() -> None:
    env = GrimEnvironment(session_id="s1")
    result = env.execute({"command": "ls -la"})
    assert result["output"] == PROTOCOL_REMINDER
    assert result["returncode"] == 1


def test_valid_write_then_run_round_trips_through_the_db() -> None:
    env = GrimEnvironment(session_id="s1")
    write_result = env.execute(_write_action("greet", "python", "prints a greeting", "print('hi')"))
    assert write_result["returncode"] == 0
    assert "wrote greet@1" in write_result["output"]

    run_result = env.execute({"command": "grim run greet"})
    assert run_result["returncode"] == 0
    assert "hi" in run_result["output"]


def test_two_executions_share_session_and_increment_seq() -> None:
    env = GrimEnvironment(session_id="fixed-session")
    env.execute(_write_action("greet", "python", "d", "print('hi')"))
    first = env.execute({"command": "grim run greet"})
    second = env.execute({"command": "grim run greet"})

    assert "exec #" in first["output"]
    assert "exec #" in second["output"]
    first_id = first["output"].split("exec #")[1].split(" ")[0]
    second_id = second["output"].split("exec #")[1].split(" ")[0]
    assert first_id != second_id


def test_sentinel_with_exit_zero_raises_submitted() -> None:
    env = GrimEnvironment(session_id="s1")
    env.execute(
        _write_action("finish", "bash", "submits", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
    )
    with pytest.raises(Submitted):
        env.execute({"command": "grim run finish"})


def test_sentinel_with_nonzero_exit_does_not_submit() -> None:
    env = GrimEnvironment(session_id="s1")
    body = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT; exit 1"
    env.execute(_write_action("finish", "bash", "submits", body))
    result = env.execute({"command": "grim run finish"})
    assert result["returncode"] != 0
