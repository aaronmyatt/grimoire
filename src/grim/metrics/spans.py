"""Per-session summaries from the span log (metrics slice): what the library
DB can't know — how long a session took, how it ended, model calls, tokens,
format errors. Reads the JSONL that adapter/trace.py appends to; never
writes it. Unparseable lines are skipped: the log is evidence, not input to
trust.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from grim.metrics.formats import Cell

# The adapter's trace.py owns the log; this is a deliberate duplicate of its
# env knob and default path (slices never import each other).
INSTRUMENT_FILE_ENV = "GRIM_INSTRUMENT_FILE"
_DEFAULT_FILE = ("~", ".grimoire", "instrumentation.jsonl")


@dataclasses.dataclass
class _Session:
    """Running totals for one session id across all its span records. A
    `--continue` run reuses the id, so open/close pairs can repeat: times add
    up, and the last close decides exit_status."""

    model: str | None = None
    task: str | None = None
    opened_at: str | None = None
    exit_status: str | None = None
    wall_ms: int | None = None
    human_wait_ms: int = 0
    llm_calls: int = 0
    tokens: int = 0
    max_prompt_tokens: int = 0
    format_errors: int = 0


SPAN_FIELDS = tuple(f.name for f in dataclasses.fields(_Session))


def instrument_path() -> Path:
    """$GRIM_INSTRUMENT_FILE, else ~/.grimoire/instrumentation.jsonl."""
    raw = os.environ.get(INSTRUMENT_FILE_ENV, "").strip()
    path = Path(raw).expanduser() if raw else Path(*_DEFAULT_FILE).expanduser()
    assert path.name, "the span log path names a file"
    return path


def session_spans(path: Path, since_ms: int) -> dict[str, dict[str, Cell]]:
    """session id -> SPAN_FIELDS, for records at or after `since_ms` (epoch
    ms). A missing or unreadable log yields {} — stats degrade, never crash."""
    assert since_ms >= 0, "since is an epoch-ms lower bound"
    sessions: dict[str, _Session] = {}
    for record in _records(path):
        session = record.get("session")
        if not isinstance(session, str) or not session or _int(record.get("ts")) < since_ms:
            continue
        _fold(sessions.setdefault(session, _Session()), record)
    result = {sid: dataclasses.asdict(acc) for sid, acc in sessions.items()}
    assert all(tuple(row) == SPAN_FIELDS for row in result.values()), "fixed span columns"
    return result


def _records(path: Path) -> Iterator[dict[str, Any]]:
    """Each JSON object line of the log; bad lines and a missing file skip."""
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _fold(acc: _Session, record: dict[str, Any]) -> None:
    """Fold one span/session record into its session's running totals."""
    kind, phase = record.get("kind"), record.get("phase")
    if kind == "session" and phase == "open":
        acc.model = acc.model or _text(record.get("model"))
        acc.task = acc.task or _text(record.get("task"))
        acc.opened_at = acc.opened_at or _utc(_int(record.get("ts")))
    elif kind == "session" and phase == "close":
        acc.wall_ms = (acc.wall_ms or 0) + _int(record.get("wall_ms"))
        totals = record.get("totals") if isinstance(record.get("totals"), dict) else {}
        wait = totals.get("agent.human_wait") if isinstance(totals, dict) else None
        acc.human_wait_ms += _int(wait.get("ms")) if isinstance(wait, dict) else 0
        acc.exit_status = _text(record.get("exit_status")) or acc.exit_status
    elif phase == "llm.completion":
        acc.llm_calls += 1
        acc.tokens += _int(record.get("total_tokens"))
        acc.max_prompt_tokens = max(acc.max_prompt_tokens, _int(record.get("prompt_tokens")))
    if record.get("error") == "FormatError":
        acc.format_errors += 1


def _int(value: object) -> int:
    """A numeric log field as an int; anything else counts as 0."""
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _text(value: object) -> str | None:
    """A non-empty string log field, else None."""
    return value if isinstance(value, str) and value else None


def _utc(epoch_ms: int) -> str | None:
    """Epoch ms as SQLite's 'YYYY-MM-DD HH:MM:SS' UTC text, so span times sort
    and bucket exactly like the DB's datetime('now') columns.
    Ref: https://docs.python.org/3/library/time.html#time.strftime"""
    if epoch_ms <= 0:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch_ms / 1000))
