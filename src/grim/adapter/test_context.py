"""Unit tests for grim.adapter.context — the context-length budget.

Covers the pure parts (window/budget resolution, the message compaction
ladder, env-knob clamping, error classification, the previous-session
snippet) with synthetic messages; the litellm call path is exercised by the
adapter e2e suite. Ambient GRIM_COMPACT_* envs are pinned per-test so they
never leak in.
"""

from __future__ import annotations

from collections.abc import Iterator
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
_CAP_DEFAULT = 120_000
_CAP_MIN = 16_000
_CAP_CUSTOM = 50_000


@pytest.fixture(autouse=True)
def _context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GRIM_COMPACT",
        "GRIM_COMPACT_AT",
        "GRIM_COMPACT_KEEP",
        "GRIM_MAX_TOOL_OUTPUT",
        "GRIM_MAX_OBSERVATION",
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


def test_compact_max_tokens_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert context.compact_max_tokens() == _CAP_DEFAULT
    monkeypatch.setenv("GRIM_COMPACT_MAX_TOKENS", "5")
    assert context.compact_max_tokens() == _CAP_MIN
    monkeypatch.setenv("GRIM_COMPACT_MAX_TOKENS", "garbage")
    assert context.compact_max_tokens() == _CAP_DEFAULT


def test_compaction_threshold_caps_a_huge_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # A ~1M-token model: the window share alone would wait until ~740k.
    monkeypatch.setitem(context._MODEL_INFO, "big/model", {"window": 1_000_000, "reserve": 8_000})
    assert context.compaction_threshold("big/model") == _CAP_DEFAULT
    monkeypatch.setenv("GRIM_COMPACT_MAX_TOKENS", "50000")
    assert context.compaction_threshold("big/model") == _CAP_CUSTOM


def test_compaction_threshold_keeps_the_window_share_when_lower() -> None:
    expected = int(context.budget_for("gpt-4o") * context.compact_at())
    assert expected < _CAP_DEFAULT
    assert context.compaction_threshold("gpt-4o") == expected


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


# --- observation cap (one new tool result, at ingestion) ---------------------

_OBS_LIMIT = 4_096
_OBS_DEFAULT = 20_000
_OBS_MAX = 262_144


def test_cap_observation_leaves_short_text_unchanged() -> None:
    text = "<returncode>0</returncode>\n<output>\nhi\n</output>"
    assert context.cap_observation(text, _OBS_LIMIT) == text


def test_cap_observation_keeps_head_and_tail_and_names_the_exec_to_page() -> None:
    text = "<output>\n[grim] exec #4242 · grep@1\nHEAD" + "x" * 50_000 + "TAIL\n</output>"
    capped = context.cap_observation(text, _OBS_LIMIT)
    assert capped.startswith("<output>\n[grim] exec #4242")
    assert "TAIL\n</output>" in capped
    assert "read(exec=4242, page=N)" in capped
    assert len(capped) < _OBS_LIMIT + 200


def test_cap_observation_without_an_exec_id_points_at_head_tail() -> None:
    capped = context.cap_observation("y" * 10_000, _OBS_LIMIT)
    assert "head/tail" in capped
    assert "read(exec=" not in capped


def test_max_observation_default_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert context.max_observation() == _OBS_DEFAULT
    monkeypatch.setenv("GRIM_MAX_OBSERVATION", "10")
    assert context.max_observation() == _OBS_LIMIT
    monkeypatch.setenv("GRIM_MAX_OBSERVATION", "99999999")
    assert context.max_observation() == _OBS_MAX
    monkeypatch.setenv("GRIM_MAX_OBSERVATION", "lots")
    assert context.max_observation() == _OBS_DEFAULT
