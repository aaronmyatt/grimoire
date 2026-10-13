"""Read-only library queries for `grim stats` (metrics slice): raw rows from
the kernel schema (db.py), one dict per execution / script / session.

Definitions every dataset shares:
- escape hatch: a run of a script that runs arbitrary commands — the retired
  `shell` seed, the agent-made `run_sh`, and the no-shell `run_cmd`/`run_py`.
- reuse: a run of a NON-seed script written in a different session. Seeds
  carry origin 'human-adhoc', so without `seeded = 0` every seed run would
  count as reuse (the inflated-reuse bug in docs/metrics-research.md).
`since` is a 'YYYY-MM-DD' lower bound ('' = all time), compared as text
against SQLite's 'YYYY-MM-DD HH:MM:SS' UTC timestamps.
"""

from __future__ import annotations

import sqlite3

from grim.metrics.formats import Cell

# Duplicated in seeds/bodies.py (stats) and evals/harness_health.py — flagged
# in ABSTRACTIONS.md; slices never import each other.
ESCAPE_HATCHES = ("shell", "run_sh", "run_cmd", "run_py")
_HATCH_PARAMS = ", ".join("?" for _ in ESCAPE_HATCHES)
_IS_REUSE = (
    "(s.seeded = 0 AND s.origin_session_id IS NOT NULL AND s.origin_session_id != e.session_id)"
)

_RUNS_SQL = (
    "SELECT e.id AS exec_id, e.session_id, COALESCE(ses.kind, 'agent') AS session_kind, "
    "e.seq, s.name AS script, COALESCE(v.language, s.language) AS language, v.version, "
    f"s.seeded, s.name IN ({_HATCH_PARAMS}) AS escape_hatch, {_IS_REUSE} AS reuse, "
    "e.exit_code, e.duration_ms, e.started_at "
    "FROM execution e JOIN script_version v ON v.id = e.script_version_id "
    "JOIN script s ON s.id = v.script_id LEFT JOIN session ses ON ses.id = e.session_id "
    "WHERE e.started_at >= ? ORDER BY e.id"
)

_SCRIPTS_SQL = (
    "SELECT s.name AS script, s.language, s.seeded, s.archived, "
    "s.name GLOB 'scratch_*' AS scratch, s.scope, s.created_at, s.origin_session_id, "
    "(SELECT MAX(v2.version) FROM script_version v2 WHERE v2.script_id = s.id) AS versions, "
    "COUNT(e.id) AS runs, COALESCE(SUM(e.exit_code != 0), 0) AS failed_runs, "
    "COUNT(DISTINCT e.session_id) AS sessions, "
    "MIN(CASE WHEN s.seeded = 0 AND e.session_id != s.origin_session_id "
    "THEN e.started_at END) AS first_reuse_at, "
    "MAX(e.started_at) AS last_run_at "
    "FROM script s LEFT JOIN script_version v ON v.script_id = s.id "
    "LEFT JOIN execution e ON e.script_version_id = v.id AND e.started_at >= ? "
    "GROUP BY s.id ORDER BY s.id"
)

_SESSIONS_SQL = (
    "SELECT ses.id AS session_id, ses.kind, ses.model, ses.task, ses.started_at, "
    "COUNT(e.id) AS runs, COALESCE(SUM(e.exit_code != 0), 0) AS failed_runs, "
    f"COALESCE(SUM(s.name IN ({_HATCH_PARAMS})), 0) AS escape_runs, "
    "COALESCE(SUM(s.seeded = 1), 0) AS seed_runs, "
    f"COALESCE(SUM({_IS_REUSE}), 0) AS reuse_runs, "
    "(SELECT COUNT(*) FROM script w WHERE w.origin_session_id = ses.id AND w.seeded = 0) "
    "AS scripts_written "
    "FROM session ses LEFT JOIN execution e ON e.session_id = ses.id "
    "LEFT JOIN script_version v ON v.id = e.script_version_id "
    "LEFT JOIN script s ON s.id = v.script_id "
    "WHERE ses.started_at >= ? GROUP BY ses.id ORDER BY ses.started_at, ses.id"
)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Cell, ...]) -> list[dict[str, Cell]]:
    """Run one SELECT and return plain dicts (column name -> value)."""
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]
    assert all(len(row) == len(names) for row in rows), "every row carries every column"
    return rows


def run_rows(conn: sqlite3.Connection, since: str) -> list[dict[str, Cell]]:
    """One row per execution at or after `since`, oldest first."""
    rows = _rows(conn, _RUNS_SQL, (*ESCAPE_HATCHES, since))
    assert all(str(row["started_at"] or "") >= since for row in rows), "since filter holds"
    return rows


def script_rows(conn: sqlite3.Connection, since: str) -> list[dict[str, Cell]]:
    """One row per script (archived included); run counts are since `since`."""
    rows = _rows(conn, _SCRIPTS_SQL, (since,))
    assert all(isinstance(row["runs"], int) for row in rows), "COUNT is always an int"
    return rows


def session_rows(conn: sqlite3.Connection, since: str) -> list[dict[str, Cell]]:
    """One row per session started at or after `since`, with its run totals."""
    rows = _rows(conn, _SESSIONS_SQL, (*ESCAPE_HATCHES, since))
    assert all(_int(row["runs"]) >= _int(row["failed_runs"]) for row in rows), "fails are runs"
    return rows


def _int(value: Cell) -> int:
    """A COUNT/SUM cell as an int (SQLite hands these back as int or None)."""
    assert value is None or isinstance(value, int), "aggregate cells are ints"
    return value or 0
