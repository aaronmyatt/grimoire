"""Tests for metrics/queries.py against a small hand-built library (raw SQL
on the kernel schema; no verbs import — slices stay independent)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.metrics import queries

# (session id, kind, started_at)
_SESSIONS = (
    ("human-adhoc", "human", "2026-09-01 00:00:00"),
    ("s1", "agent", "2026-10-01 09:00:00"),
    ("s2", "agent", "2026-10-08 09:00:00"),
)
# (name, seeded, origin session)
_SCRIPTS = (
    ("read_file", 1, "human-adhoc"),
    ("run_cmd", 1, "human-adhoc"),
    ("fetch_page", 0, "s1"),
    ("scratch_probe", 0, "s2"),
)
# (session, seq, script, exit code, started_at)
_RUNS = (
    ("s1", 1, "read_file", 0, "2026-10-01 09:01:00"),
    ("s1", 2, "fetch_page", 0, "2026-10-01 09:02:00"),
    ("s1", 3, "fetch_page", 1, "2026-10-01 09:03:00"),
    ("s2", 1, "fetch_page", 0, "2026-10-08 09:01:00"),
    ("s2", 2, "run_cmd", 0, "2026-10-08 09:02:00"),
    ("s2", 3, "scratch_probe", 0, "2026-10-08 09:03:00"),
    ("human-adhoc", 1, "fetch_page", 0, "2026-10-09 10:00:00"),
)
_ALL_TIME = ""


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "grimoire.db")
    db.migrate(connection)
    connection.executemany("INSERT INTO session (id, kind, started_at) VALUES (?, ?, ?)", _SESSIONS)
    for name, seeded, origin in _SCRIPTS:
        script_id = connection.execute(
            "INSERT INTO script (name, language, description, seeded, origin_session_id) "
            "VALUES (?, 'python', 'd', ?, ?)",
            (name, seeded, origin),
        ).lastrowid
        connection.execute(
            "INSERT INTO script_version (script_id, version, body, body_hash) "
            "VALUES (?, 1, 'print(1)', ?)",
            (script_id, f"h-{name}"),
        )
    for session, seq, name, code, when in _RUNS:
        connection.execute(
            "INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) "
            "SELECT v.id, ?, ?, ?, ? FROM script_version v JOIN script s ON s.id = v.script_id "
            "WHERE s.name = ?",
            (session, seq, code, when, name),
        )
    connection.commit()
    return connection


def test_run_rows_flag_reuse_without_counting_seeds(conn: sqlite3.Connection) -> None:
    rows = queries.run_rows(conn, _ALL_TIME)
    assert len(rows) == len(_RUNS)
    reuse = {(r["session_id"], r["seq"]): r["reuse"] for r in rows}
    assert reuse[("s1", 1)] == 0  # a seed run is never reuse (the inflated-reuse bug)
    assert reuse[("s1", 2)] == 0  # fetch_page in the session that wrote it
    assert reuse[("s2", 1)] == 1  # fetch_page in a later session
    assert reuse[("human-adhoc", 1)] == 1
    hatches = [r["script"] for r in rows if r["escape_hatch"]]
    assert hatches == ["run_cmd"]


def test_run_rows_honor_since(conn: sqlite3.Connection) -> None:
    rows = queries.run_rows(conn, "2026-10-05")
    assert {r["session_id"] for r in rows} == {"s2", "human-adhoc"}
    assert rows[-1]["session_kind"] == "human"


def test_script_rows_count_runs_sessions_and_first_reuse(conn: sqlite3.Connection) -> None:
    rows = {r["script"]: r for r in queries.script_rows(conn, _ALL_TIME)}
    fetch = rows["fetch_page"]
    assert (fetch["runs"], fetch["failed_runs"], fetch["sessions"]) == (4, 1, 3)
    assert fetch["first_reuse_at"] == "2026-10-08 09:01:00"
    assert rows["scratch_probe"]["scratch"] == 1
    assert rows["read_file"]["first_reuse_at"] is None  # seeds are never "reused"


def test_script_rows_keep_unrun_scripts_with_zero_runs(conn: sqlite3.Connection) -> None:
    rows = {r["script"]: r for r in queries.script_rows(conn, "2026-12-01")}
    assert set(rows) == {name for name, _, _ in _SCRIPTS}
    assert all(r["runs"] == 0 and r["last_run_at"] is None for r in rows.values())


def test_session_rows_total_each_sessions_runs(conn: sqlite3.Connection) -> None:
    rows = {r["session_id"]: r for r in queries.session_rows(conn, _ALL_TIME)}
    s1, s2 = rows["s1"], rows["s2"]
    assert (s1["runs"], s1["failed_runs"], s1["seed_runs"], s1["reuse_runs"]) == (3, 1, 1, 0)
    assert (s2["runs"], s2["escape_runs"], s2["reuse_runs"]) == (3, 1, 1)
    assert (s1["scripts_written"], s2["scripts_written"]) == (1, 1)
    assert list(queries.session_rows(conn, "2026-10-05")) == [rows["s2"]]
