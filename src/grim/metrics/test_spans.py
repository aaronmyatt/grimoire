"""Tests for metrics/spans.py — a hand-written span log, folded per session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grim.metrics import spans

_T0 = 1_791_000_000_000  # epoch ms, 2026-10-03
_OLD = _T0 - 86_400_000  # one day earlier
_WALL_MS = 10_000
_WAIT_MS = 4_000
_TOKENS = (100, 250)
_PROMPTS = (80, 200)


def _write(path: Path, records: list[object]) -> Path:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n")
    return path


def _log(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "instrumentation.jsonl",
        [
            {
                "kind": "session",
                "phase": "open",
                "session": "a",
                "ts": _T0,
                "model": "m1",
                "task": "fix it",
            },
            {
                "kind": "span",
                "phase": "llm.completion",
                "session": "a",
                "ts": _T0 + 1,
                "total_tokens": _TOKENS[0],
                "prompt_tokens": _PROMPTS[0],
            },
            {
                "kind": "span",
                "phase": "llm.parse",
                "session": "a",
                "ts": _T0 + 2,
                "error": "FormatError",
                "error_msg": "no tool call",
            },
            {
                "kind": "span",
                "phase": "llm.completion",
                "session": "a",
                "ts": _T0 + 3,
                "total_tokens": _TOKENS[1],
                "prompt_tokens": _PROMPTS[1],
            },
            "{not json",
            {"kind": "span", "phase": "agent.boot", "ts": _T0},  # no session: ignored
            {
                "kind": "session",
                "phase": "close",
                "session": "a",
                "ts": _T0 + 9,
                "wall_ms": _WALL_MS,
                "exit_status": "Submitted",
                "totals": {"agent.human_wait": {"count": 1, "ms": _WAIT_MS}},
            },
            {"kind": "session", "phase": "open", "session": "old", "ts": _OLD},
        ],
    )


def test_session_spans_fold_one_sessions_records(tmp_path: Path) -> None:
    row = spans.session_spans(_log(tmp_path), 0)["a"]
    assert (row["model"], row["task"], row["exit_status"]) == ("m1", "fix it", "Submitted")
    assert (row["wall_ms"], row["human_wait_ms"]) == (_WALL_MS, _WAIT_MS)
    assert (row["llm_calls"], row["tokens"]) == (2, sum(_TOKENS))
    assert (row["max_prompt_tokens"], row["format_errors"]) == (max(_PROMPTS), 1)
    assert row["opened_at"] == "2026-10-03 04:00:00"  # epoch _T0 in UTC


def test_session_spans_honor_since_and_skip_junk(tmp_path: Path) -> None:
    assert set(spans.session_spans(_log(tmp_path), 0)) == {"a", "old"}
    assert set(spans.session_spans(_log(tmp_path), _T0)) == {"a"}


def test_continued_session_adds_wall_time_and_keeps_last_exit(tmp_path: Path) -> None:
    log = _write(
        tmp_path / "log.jsonl",
        [
            {
                "kind": "session",
                "phase": "close",
                "session": "c",
                "ts": _T0,
                "wall_ms": 5,
                "exit_status": "KeyboardInterrupt",
            },
            {
                "kind": "session",
                "phase": "close",
                "session": "c",
                "ts": _T0 + 1,
                "wall_ms": 7,
                "exit_status": "Submitted",
            },
        ],
    )
    row = spans.session_spans(log, 0)["c"]
    assert (row["wall_ms"], row["exit_status"]) == (12, "Submitted")


def test_missing_log_yields_nothing(tmp_path: Path) -> None:
    assert spans.session_spans(tmp_path / "absent.jsonl", 0) == {}


def test_instrument_path_honors_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(spans.INSTRUMENT_FILE_ENV, str(tmp_path / "x.jsonl"))
    assert spans.instrument_path() == tmp_path / "x.jsonl"
    monkeypatch.delenv(spans.INSTRUMENT_FILE_ENV)
    assert spans.instrument_path().name == "instrumentation.jsonl"
