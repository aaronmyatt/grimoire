"""Tests for adapter/environment.py's GrimEnvironment — the six-verb
enforcement point. No live model: execute() is called directly with
scripted action dicts against a real tmp GRIM_DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

from minisweagent.exceptions import Submitted  # noqa: E402

from grim.adapter.environment import PROTOCOL_REMINDER, GrimEnvironment  # noqa: E402


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


def test_reading_a_script_containing_the_sentinel_does_not_submit() -> None:
    # Regression: `grim read` merely DISPLAYS a script's body/preview. A
    # script whose body literally contains a bare sentinel line (like the
    # explain_grimiore_prompt script that reproduced the finish protocol
    # inside a triple-quoted string) must NOT finish the task when read —
    # doing so trapped the agent in a re-read → re-submit loop.
    env = GrimEnvironment(session_id="s1")
    sentinel = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    body = f'print("""To finish, output this on its own line:\n{sentinel}\n""")'
    env.execute(_write_action("explain_finish", "python", "explains finishing", body))

    read = env.execute({"command": "grim read explain_finish"})
    # The read output really does contain a bare sentinel line (the whole
    # premise of the bug) ...
    assert any(line.strip() == sentinel for line in read["output"].splitlines())
    # ... yet reading it must return normally, not raise Submitted.
    assert read["returncode"] == 0


# Regression: argparse calls sys.exit() on malformed input rather than
# returning. Since execute() runs cli.main() in-process, that SystemExit
# (a BaseException mini's `run()` loop doesn't catch) used to tear through
# the whole agent and end the session mid-turn. _invoke now traps it and
# hands back a normal nonzero-returncode observation the agent can read
# and fix. These pin that behavior so the hole can't silently reopen.
@pytest.mark.parametrize(
    "command",
    [
        "grim update greet <<'EOF'\nprint('x')\nEOF",  # missing required --changelog
        "grim run greet --timeout notanumber",  # --timeout wants a float
        "grim list --bogus",  # unknown flag
        "grim find",  # missing required positional 'query'
    ],
)
def test_malformed_action_returns_error_observation_not_systemexit(command: str) -> None:
    env = GrimEnvironment(session_id="s1")
    # No pytest.raises(SystemExit): the point is that execute() must *return*.
    result = env.execute({"command": command})
    assert result["returncode"] != 0
    # argparse writes its usage/error to the captured stderr — the agent
    # needs that text to self-correct, so it must survive into the output.
    assert "usage:" in result["output"]


def test_help_action_returns_zero_without_submitting() -> None:
    # --help exits 0; _invoke must normalize that to returncode 0 without
    # tripping the submit sentinel (returncode 0 alone must never submit).
    env = GrimEnvironment(session_id="s1")
    result = env.execute({"command": "grim update --help"})
    assert result["returncode"] == 0
    assert "usage:" in result["output"]
