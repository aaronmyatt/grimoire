"""The datasets `grim stats` can print (metrics slice). Three are raw —
runs, scripts, sessions: one row per thing, for a visualisation layer to
slice any way it likes. Two roll those rows up — weekly and summary — into
the scorecard docs/metrics-research.md proposes (columns: periods.py).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Callable
from pathlib import Path

from grim.metrics import periods, queries, sessions
from grim.metrics.formats import Cell, Dataset

DATASETS = ("summary", "weekly", "sessions", "runs", "scripts")
ALL_TIME_PERIOD = "all"

RUN_COLUMNS = (
    "exec_id", "session_id", "session_kind", "seq", "script", "language", "version",
    "seeded", "escape_hatch", "reuse", "exit_code", "duration_ms", "started_at",
)  # fmt: skip
SCRIPT_COLUMNS = (
    "script", "language", "seeded", "archived", "scratch", "scope", "created_at",
    "origin_session_id", "versions", "runs", "failed_runs", "sessions", "first_reuse_at",
    "last_run_at",
)  # fmt: skip

_Rows = list[dict[str, Cell]]


@dataclasses.dataclass(frozen=True)
class Source:
    """Where the numbers come from. `since` is 'YYYY-MM-DD' or '' (all time);
    `generated_at` is injected so a build is deterministic under test."""

    conn: sqlite3.Connection
    spans_path: Path
    since: str
    generated_at: str


def build(name: str, source: Source) -> Dataset:
    """The dataset called `name` (one of DATASETS). Read-only on the library."""
    assert name in DATASETS, f"unknown dataset {name!r}"
    builders: dict[str, Callable[[Source], tuple[tuple[str, ...], _Rows]]] = {
        "runs": lambda s: (RUN_COLUMNS, queries.run_rows(s.conn, s.since)),
        "scripts": lambda s: (SCRIPT_COLUMNS, queries.script_rows(s.conn, s.since)),
        "sessions": lambda s: (sessions.SESSION_COLUMNS, _sessions(s)),
        "weekly": lambda s: (periods.PERIOD_COLUMNS, _periods(s, periods.iso_week)),
        "summary": lambda s: (periods.PERIOD_COLUMNS, _periods(s, lambda _: ALL_TIME_PERIOD)),
    }
    columns, rows = builders[name](source)
    meta = {"generated_at": source.generated_at, "since": source.since or "all"}
    dataset = Dataset(name=name, columns=columns, rows=tuple(rows), meta=meta)
    assert all(tuple(row) == columns for row in dataset.rows), "builders emit full rows"
    return dataset


def _sessions(source: Source) -> _Rows:
    return sessions.session_rows(source.conn, source.spans_path, source.since)


def _periods(source: Source, key: Callable[[str], str]) -> _Rows:
    inputs = periods.Inputs(
        runs=queries.run_rows(source.conn, source.since),
        sessions=_sessions(source),
        scripts=queries.script_rows(source.conn, source.since),
        since=source.since,
    )
    return periods.period_rows(inputs, key)
