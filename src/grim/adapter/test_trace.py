"""Unit tests for grim.adapter.trace — the span instrumentation emitter.

Forces GRIM_INSTRUMENT=1 (pytest's auto-disable would otherwise silence
everything) and points GRIM_INSTRUMENT_FILE at a per-test tmp path, then
checks: spans record duration/session/turn/error, session totals land on the
close record, disabled mode writes nothing, and the JSONL stays parseable.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.adapter import trace

_MIN_MS = 10  # sleep long enough that duration_ms >= this is reliable
_SLEEP_S = _MIN_MS / 1000
_ONE = 1
_TWO = 2
_FIRST_TURN = 1
_PROMPT_TOKENS = 10
_COMPLETION_TOKENS = 5


def _reset_trace() -> None:
    """Restore trace's module globals — other test files run with
    instrumentation auto-disabled and cache that verdict in trace._disabled,
    which would silently swallow the first test here unless reset both before
    and after each test."""
    with trace._lock:
        trace._handle = None
        trace._disabled = False
        trace._current_session = None
        trace._current_turn = 0
        trace._totals.clear()
        trace._session_started.clear()


@pytest.fixture(autouse=True)
def _trace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_INSTRUMENT", "1")
    monkeypatch.setenv("GRIM_INSTRUMENT_FILE", str(tmp_path / "instrumentation.jsonl"))


@pytest.fixture(autouse=True)
def _trace_reset() -> Iterator[None]:
    _reset_trace()
    yield
    _reset_trace()


def _events() -> list[dict]:
    path = Path(os.environ["GRIM_INSTRUMENT_FILE"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_span_records_duration_session_and_turn() -> None:
    trace.session_open("s1", task="t", model="m")
    trace.turn_begin()
    with trace.span("llm.completion", model="m"):
        time.sleep(_SLEEP_S)
    trace.session_close()
    trace._flush()

    spans = [e for e in _events() if e["kind"] == "span"]
    assert len(spans) == _ONE
    span = spans[0]
    assert span["phase"] == "llm.completion"
    assert span["session"] == "s1"
    assert span["turn"] == _FIRST_TURN
    assert span["model"] == "m"
    assert span["duration_ms"] >= _MIN_MS


def test_session_totals_land_on_close() -> None:
    trace.session_open("s1")
    with trace.span("llm.completion"):
        pass
    with trace.span("tool.run"):
        pass
    trace.session_close()
    trace._flush()

    closes = [e for e in _events() if e["phase"] == "close"]
    assert len(closes) == _ONE
    totals = closes[0]["totals"]
    assert totals["llm.completion"]["count"] == _ONE
    assert totals["llm.completion"]["ms"] >= 0
    assert totals["tool.run"]["count"] == _ONE
    assert closes[0]["wall_ms"] >= 0


def test_disabled_mode_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_INSTRUMENT", "0")
    trace.session_open("s1")
    with trace.span("llm.completion"):
        pass
    trace.session_close()
    trace._flush()
    assert _events() == []


def test_error_span_records_error_and_reraises() -> None:
    trace.session_open("s1")
    with pytest.raises(ValueError):
        with trace.span("llm.parse"):
            raise ValueError("bad")
    trace.session_close()
    trace._flush()

    spans = [e for e in _events() if e["kind"] == "span"]
    assert len(spans) == _ONE
    assert spans[0]["error"] == "ValueError"


def test_update_attaches_fields() -> None:
    trace.session_open("s1")
    with trace.span("llm.completion") as sp:
        sp.update(prompt_tokens=_PROMPT_TOKENS, completion_tokens=_COMPLETION_TOKENS)
    trace.session_close()
    trace._flush()

    span = [e for e in _events() if e["kind"] == "span"][0]
    assert span["prompt_tokens"] == _PROMPT_TOKENS
    assert span["completion_tokens"] == _COMPLETION_TOKENS


def test_session_open_autocloses_previous() -> None:
    # session_open("s2") while s1 is open emits s1's close record (with its
    # totals), then the explicit session_close() closes s2 — two close
    # records total, each carrying the right session id.
    trace.session_open("s1")
    with trace.span("tool.run"):
        pass
    trace.session_open("s2")
    trace.session_close()
    trace._flush()

    closes = [e for e in _events() if e["phase"] == "close"]
    assert len(closes) == _TWO
    assert closes[0]["session"] == "s1"
    assert closes[0]["totals"]["tool.run"]["count"] == _ONE
    assert closes[1]["session"] == "s2"
    opens = [e for e in _events() if e["phase"] == "open"]
    assert [o["session"] for o in opens] == ["s1", "s2"]


def test_boot_span_has_no_session() -> None:
    with trace.span("agent.boot"):
        pass
    trace._flush()

    boot = [e for e in _events() if e["kind"] == "span"][0]
    assert boot["phase"] == "agent.boot"
    assert "session" not in boot


def test_turn_counter_advances() -> None:
    trace.session_open("s1")
    first = trace.turn_begin()
    second = trace.turn_begin()
    assert second == first + _ONE
