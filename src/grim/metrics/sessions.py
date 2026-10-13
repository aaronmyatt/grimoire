"""The `sessions` dataset (metrics slice): one row per session, joining the
library's run totals (queries.session_rows) with timing and model usage from
the span log (spans.session_spans). Either side may be missing: sessions
before the span log existed have no timing; a session that never called a
verb has no library row.
"""

from __future__ import annotations

import calendar
import sqlite3
import time
from pathlib import Path

from grim.metrics import queries, spans
from grim.metrics.formats import Cell

SESSION_COLUMNS = (
    "session_id", "kind", "model", "task", "started_at", "exit_status", "runs", "failed_runs",
    "escape_runs", "seed_runs", "reuse_runs", "scripts_written", "wall_ms", "human_wait_ms",
    "active_ms", "llm_calls", "tokens", "max_prompt_tokens", "format_errors",
)  # fmt: skip
_DB_COUNTS = ("runs", "failed_runs", "escape_runs", "seed_runs", "reuse_runs", "scripts_written")
_SPAN_COUNTS = ("llm_calls", "tokens", "max_prompt_tokens", "format_errors")


def session_rows(conn: sqlite3.Connection, spans_path: Path, since: str) -> list[dict[str, Cell]]:
    """Every session seen by either source since `since` ('YYYY-MM-DD' or ''),
    oldest first. active_ms = wall time minus time waiting on the human."""
    db_rows = {str(r["session_id"]): r for r in queries.session_rows(conn, since)}
    span_rows = spans.session_spans(spans_path, since_ms(since))
    ids = set(db_rows) | set(span_rows)
    merged = [_merge(sid, db_rows.get(sid, {}), span_rows.get(sid, {})) for sid in ids]
    merged.sort(key=lambda row: (str(row["started_at"] or ""), str(row["session_id"])))
    assert len(merged) == len(ids), "one row per session id"
    return merged


def _merge(sid: str, db: dict[str, Cell], span: dict[str, Cell]) -> dict[str, Cell]:
    """One session's row. Library fields win for model/task (they are stamped
    on purpose); the span log fills them for older sessions, and its open
    time is the truer start than the first verb call's row insert."""
    wall, wait = span.get("wall_ms"), span.get("human_wait_ms")
    row: dict[str, Cell] = {
        "session_id": sid,
        "kind": db.get("kind") or "agent",
        "model": db.get("model") or span.get("model"),
        "task": db.get("task") or span.get("task"),
        "started_at": span.get("opened_at") or db.get("started_at"),
        "exit_status": span.get("exit_status"),
        **{name: num(db.get(name)) for name in _DB_COUNTS},
        "wall_ms": wall,
        "human_wait_ms": wait,
        "active_ms": num(wall) - num(wait) if wall is not None else None,
        **{name: span.get(name) for name in _SPAN_COUNTS},
    }
    assert tuple(row) == SESSION_COLUMNS, "merged rows follow SESSION_COLUMNS"
    return row


def since_ms(since: str) -> int:
    """'YYYY-MM-DD' (UTC midnight) as epoch ms; '' is 0 (all time).
    Ref: https://docs.python.org/3/library/calendar.html#calendar.timegm"""
    if not since:
        return 0
    result = calendar.timegm(time.strptime(since, "%Y-%m-%d")) * 1000
    assert result > 0, "a calendar date after 1970 is a positive epoch"
    return result


def num(value: Cell) -> int:
    """A numeric cell as an int (None counts as 0)."""
    assert value is None or isinstance(value, int | float), "numeric cells only"
    return int(value or 0)
