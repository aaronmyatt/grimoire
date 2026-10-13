"""Tests for GrimAgent.run's session close record: the instrumentation log
says WHY each session ended (submitted, interrupted, crashed). Kept apart
from test_agent.py, which is already over the file-length budget."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `agent` extra

from minisweagent.agents.interactive import InteractiveAgent  # noqa: E402
from minisweagent.models.test_models import DeterministicModel, make_output  # noqa: E402

from grim import db  # noqa: E402
from grim.adapter import trace  # noqa: E402
from grim.adapter.agent import GrimAgent  # noqa: E402
from grim.adapter.environment import GrimEnvironment  # noqa: E402
from grim.adapter.tools import render_command  # noqa: E402


@pytest.fixture(autouse=True)
def _instrumented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Instrumentation is auto-off under pytest and caches that verdict in
    trace's globals: force it on into a tmp file, and reset the globals."""
    log = tmp_path / "instrumentation.jsonl"
    monkeypatch.setenv("GRIM_INSTRUMENT", "1")
    monkeypatch.setenv("GRIM_INSTRUMENT_FILE", str(log))
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setattr(trace, "_disabled", False)
    monkeypatch.setattr(trace, "_handle", None)
    monkeypatch.setattr(trace, "_current_session", None)
    db.migrate(db.connect())
    yield log


def _closes(log: Path) -> list[dict[str, Any]]:
    trace._flush()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    return [r for r in records if r["kind"] == "session" and r["phase"] == "close"]


def _agent(outputs: list[dict[str, object]]) -> GrimAgent:
    return GrimAgent(
        DeterministicModel(outputs=outputs),  # type: ignore[no-untyped-call]
        GrimEnvironment(session_id="close-test"),
        system_template="You are a test agent.",
        instance_template="{{task}}",
        mode="yolo",
        cost_limit=0,
    )


def _submit(result: str) -> dict[str, object]:
    args = {"result": result}
    action = {"tool": "submit", "args": args, "tool_call_id": "c"}
    return make_output("done", [action | {"command": render_command("submit", args)}])


def _press_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    def _prompt(self: object, prompt: str, *, _multiline: bool = False) -> str:
        return ""

    prompt: Callable[..., str] = _prompt
    monkeypatch.setattr(InteractiveAgent, "_prompt_and_handle_slash_commands", prompt)


def test_submitted_run_closes_with_its_exit_status(
    _instrumented: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _press_enter(monkeypatch)
    _agent([_submit("ok")]).run("task")

    closes = _closes(_instrumented)
    assert len(closes) == 1
    assert closes[0]["exit_status"] == "Submitted"


def test_crashed_run_closes_with_the_exception_name(
    _instrumented: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("model went away")

    monkeypatch.setattr(GrimAgent, "_run_loop", _boom)
    with pytest.raises(RuntimeError):
        _agent([]).run("task")

    closes = _closes(_instrumented)
    assert [c["exit_status"] for c in closes] == ["RuntimeError"]
