"""Tests for metrics/sessions.py — library rows joined with a span log."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.metrics import sessions

_OPEN_MS = 1_791_190_800_000  # 2026-10-05 09:00:00 UTC
_WALL_MS, _WAIT_MS = 60_000, 20_000
_OCT5_MS = 1_791_158_400_000  # 2026-10-05 00:00:00 UTC
_TRUNCATED = 2  # num(2.9) truncates, like int()

_SQL = """
INSERT INTO session (id, kind, started_at) VALUES
  ('s1', 'agent', '2026-10-05 09:00:30'), ('s2', 'agent', '2026-10-06 09:00:00');
INSERT INTO script (id, name, language, description, seeded, origin_session_id)
VALUES (1, 'fetch_page', 'python', 'd', 0, 's1');
INSERT INTO script_version (id, script_id, version, body, body_hash) VALUES (1, 1, 1, 'b', 'h');
INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) VALUES
  (1, 's1', 1, 1, '2026-10-05 09:02:00'), (1, 's2', 1, 0, '2026-10-06 09:01:00');
"""


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "grimoire.db")
    db.migrate(connection)
    connection.executescript(_SQL)
    return connection


def _log(path: Path) -> Path:
    records = [
        {"kind": "session", "phase": "open", "session": "s1", "ts": _OPEN_MS, "model": "m"},
        {"kind": "span", "phase": "llm.completion", "session": "s1", "ts": _OPEN_MS + 1,
         "total_tokens": 300},
        {"kind": "session", "phase": "close", "session": "s1", "ts": _OPEN_MS + 2,
         "wall_ms": _WALL_MS, "exit_status": "Submitted",
         "totals": {"agent.human_wait": {"count": 1, "ms": _WAIT_MS}}},
        {"kind": "session", "phase": "open", "session": "s3", "ts": _OPEN_MS + 5},
    ]  # fmt: skip
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_rows_join_library_totals_with_span_timing(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    rows = {r["session_id"]: r for r in sessions.session_rows(conn, _log(tmp_path / "l"), "")}
    s1 = rows["s1"]
    assert tuple(s1) == sessions.SESSION_COLUMNS
    assert (s1["model"], s1["exit_status"], s1["started_at"]) == (
        "m",
        "Submitted",
        "2026-10-05 09:00:00",
    )
    assert (s1["runs"], s1["failed_runs"], s1["scripts_written"]) == (1, 1, 1)
    assert (s1["active_ms"], s1["tokens"]) == (_WALL_MS - _WAIT_MS, 300)
    assert (rows["s2"]["reuse_runs"], rows["s2"]["wall_ms"], rows["s2"]["active_ms"]) == (
        1,
        None,
        None,
    )
    assert (rows["s3"]["runs"], rows["s3"]["kind"]) == (0, "agent")  # span-only session


def test_rows_are_oldest_first_and_honor_since(conn: sqlite3.Connection, tmp_path: Path) -> None:
    log = _log(tmp_path / "l")
    assert [r["session_id"] for r in sessions.session_rows(conn, log, "")] == ["s1", "s3", "s2"]
    assert [r["session_id"] for r in sessions.session_rows(conn, log, "2026-10-06")] == ["s2"]


def test_since_ms_and_num() -> None:
    assert sessions.since_ms("") == 0
    assert sessions.since_ms("2026-10-05") == _OCT5_MS
    assert sessions.num(None) == 0 and sessions.num(2.9) == _TRUNCATED
