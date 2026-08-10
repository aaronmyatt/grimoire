"""Unit tests for grim.adapter.context — the context-length budget.

Covers the pure parts (window/budget resolution, the message compaction
ladder, env-knob clamping, error classification, the previous-session
snippet) with synthetic messages; the litellm call path is exercised by the
adapter e2e suite. Ambient GRIM_COMPACT_* envs are pinned per-test so they
never leak in.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from grim.adapter import context

_MAX_OUTPUT = 1000
_CAP_TURNS = 6
_SUMMARY_KEEP = 2
_TURNS = 8
_NOOP_TURNS = 2
_NOOP_OUTPUT = 60_000
_AT_DEFAULT = 0.75
_AT_MAX = 0.95
_KEEP_DEFAULT = 6
_OUTPUT_DEFAULT = 4096
_KEEP_MIN = 1
_OUTPUT_MIN = 512
_BUDGET_LO = 100_000
_WINDOW = 128_000
_AUTHORITATIVE = 99_999


@pytest.fixture(autouse=True)
def _context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GRIM_COMPACT",
        "GRIM_COMPACT_AT",
        "GRIM_COMPACT_KEEP",
        "GRIM_MAX_TOOL_OUTPUT",
        "GRIM_COMPACT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    context._LAST_PROMPT_TOKENS = None
    context._MODEL_INFO.clear()
    yield
    context._LAST_PROMPT_TOKENS = None
    context._MODEL_INFO.clear()


_SYSTEM = {"role": "system", "content": "be an agent"}
_TASK = {"role": "user", "content": "solve this"}
_LONG_OUTPUT = "x" * 5000


def _assistant_turn(i: int, with_result: bool = True) -> list[dict[str, Any]]:
    tool_call = {
        "tool": "run",
        "args": {"name": f"s{i}"},
        "tool_call_id": f"tc{i}",
        "command": f"run s{i}",
    }
    messages = [{"role": "assistant", "content": f"reasoning {i}", "tool_calls": [tool_call]}]
    if with_result:
        messages.append(
            {"role": "tool", "content": f"output-{i}-" + _LONG_OUTPUT, "tool_call_id": f"tc{i}"}
        )
    return messages


def _conversation(turns: int = _TURNS, with_results: bool = True) -> list[dict[str, Any]]:
    messages = [_SYSTEM, _TASK]
    for i in range(turns):
        messages += _assistant_turn(i, with_results)
    return messages


def test_compact_trims_tool_results() -> None:
    messages = _conversation(3)
    out, note = context.compact_messages(messages, keep_turns=_CAP_TURNS, max_output=_MAX_OUTPUT)
    assert note == "trimmed tool results"
    for m in out:
        if m.get("role") == "tool":
            assert len(m["content"]) <= _MAX_OUTPUT  # the cap is guaranteed
            assert "[truncated" in m["content"]
    assert out[0] == _SYSTEM and out[1] == _TASK  # system + task survive


def test_compact_summarizes_old_span() -> None:
    messages = _conversation()
    out, note = context.compact_messages(
        messages, keep_turns=_SUMMARY_KEEP, max_output=_MAX_OUTPUT, summary_text="SUMMARY"
    )
    assert "summarized 6 earlier turns" in note
    assert [m["role"] for m in out][:3] == ["system", "user", "user"]
    assert out[2]["content"] == "SUMMARY"
    joined = " ".join(str(m.get("content", "")) for m in out)
    assert "reasoning 7" in joined and "reasoning 6" in joined  # tail kept
    assert "reasoning 0" not in joined and "output-0" not in joined  # old gone
    assert len(out) < len(messages)


def test_compact_drops_stale_results_without_summary() -> None:
    messages = _conversation()
    out, note = context.compact_messages(messages, keep_turns=_SUMMARY_KEEP, max_output=_MAX_OUTPUT)
    assert "dropped 6 stale tool results" in note
    ids = [m.get("tool_call_id") for m in out]
    assert "tc6" in ids and "tc7" in ids  # kept turns' results survive
    assert "tc0" not in ids and "tc1" not in ids  # stale results dropped
    # assistant tool-calls are all kept; only their results are dropped
    assert [m["role"] for m in out].count("assistant") == _TURNS


def test_compact_noop_when_few_turns() -> None:
    messages = _conversation(_NOOP_TURNS)
    out, note = context.compact_messages(messages, keep_turns=_CAP_TURNS, max_output=_NOOP_OUTPUT)
    assert note == "no-op"
    assert out == messages


def test_compact_never_mutates_input() -> None:
    messages = _conversation()
    before = [dict(m) for m in messages]
    context.compact_messages(
        messages, keep_turns=_SUMMARY_KEEP, max_output=_MAX_OUTPUT, summary_text="S"
    )
    assert messages == before


def test_knobs_defaults() -> None:
    assert context.enabled()
    assert context.compact_at() == _AT_DEFAULT
    assert context.keep() == _KEEP_DEFAULT
    assert context.max_tool_output() == _OUTPUT_DEFAULT
    assert context.compact_model() is None


def test_knobs_validate_external_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_COMPACT", "0")
    assert not context.enabled()
    monkeypatch.setenv("GRIM_COMPACT", "off")
    assert not context.enabled()
    monkeypatch.setenv("GRIM_COMPACT_AT", "999")
    assert context.compact_at() == _AT_MAX
    monkeypatch.setenv("GRIM_COMPACT_AT", "garbage")
    assert context.compact_at() == _AT_DEFAULT
    monkeypatch.setenv("GRIM_COMPACT_KEEP", "0")
    assert context.keep() == _KEEP_MIN
    monkeypatch.setenv("GRIM_MAX_TOOL_OUTPUT", "1")
    assert context.max_tool_output() == _OUTPUT_MIN
    monkeypatch.setenv("GRIM_COMPACT_MODEL", "  cheap/model  ")
    assert context.compact_model() == "cheap/model"


def test_budget_for_falls_back_on_unmapped_model() -> None:
    budget = context.budget_for("not/a/real/model-xyz")
    assert budget >= _WINDOW // 2  # conservative default window, never tiny


def test_budget_for_known_model() -> None:
    budget = context.budget_for("gpt-4o")
    assert _BUDGET_LO < budget <= _WINDOW


def test_prompt_tokens_uses_authoritative_count() -> None:
    messages = [_SYSTEM, _TASK]
    assert context.prompt_tokens(messages, "gpt-4o") > 0
    fake = type("R", (), {"usage": type("U", (), {"prompt_tokens": _AUTHORITATIVE})})()
    context._remember(fake)
    assert context.prompt_tokens(messages, "gpt-4o") == _AUTHORITATIVE


def test_is_context_error_classification() -> None:
    import litellm

    assert context.is_context_error(
        litellm.exceptions.ContextWindowExceededError("boom", "gpt-4o", "openai")
    )
    assert context.is_context_error(
        litellm.exceptions.BadRequestError(
            "This model's maximum context length is 128000 tokens", "gpt-4o", "openai"
        )
    )
    assert not context.is_context_error(ValueError("nope"))
    assert not context.is_context_error(
        litellm.exceptions.BadRequestError("bad json", "gpt-4o", "openai")
    )


def test_previous_session_snippet(tmp_path: Path) -> None:
    traj = tmp_path / "grimoire-test.traj.json"
    traj.write_text(
        '{"info": {"exit_status": "Submitted"}, "messages": ['
        '{"role": "user", "content": "task one"},'
        '{"role": "assistant", "content": "reasoning"},'
        '{"role": "tool", "content": "output"}]}'
    )
    pointer = tmp_path / "last-trajectory"
    pointer.write_text(str(traj) + "\n")
    snippet = context.previous_session_snippet(pointer)
    assert "Submitted" in snippet
    assert "reasoning" in snippet and "output" in snippet


def test_previous_session_snippet_degrades(tmp_path: Path) -> None:
    assert context.previous_session_snippet(tmp_path / "absent") == ""
    bad = tmp_path / "bad.traj.json"
    bad.write_text("not json")
    pointer = tmp_path / "p"
    pointer.write_text(str(bad) + "\n")
    assert context.previous_session_snippet(pointer) == ""
    other = tmp_path / "other"
    other.write_text("x")
    pointer.write_text(str(other) + "\n")
    assert context.previous_session_snippet(pointer) == ""
