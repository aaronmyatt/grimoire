"""Tests for curate/recent.py."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.curate import _shared
from grim.curate.recent import cmd_recent, recent_scripts
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _write(conn: sqlite3.Connection, name: str) -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description="d",
            body="print(1)",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def _record_run(conn: sqlite3.Connection, name: str, started_at: str) -> None:
    version = conn.execute(
        "SELECT sv.id FROM script_version sv JOIN script s ON s.id = sv.script_id "
        "WHERE s.name = ? ORDER BY sv.version DESC LIMIT 1",
        (name,),
    ).fetchone()
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES ('human-adhoc', 'human')")
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM execution WHERE session_id = 'human-adhoc'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) "
        "VALUES (?, 'human-adhoc', ?, 0, ?)",
        (version["id"], seq, started_at),
    )
    conn.commit()


def test_recent_scripts_orders_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "old_one")
    _write(conn, "new_one")
    _record_run(conn, "old_one", "2020-01-01 00:00:00")
    _record_run(conn, "new_one", "2025-01-01 00:00:00")
    rows = recent_scripts(conn)
    assert [row["name"] for row in rows] == ["new_one", "old_one"]


def test_recent_scripts_excludes_never_run_and_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "ran_one")
    _write(conn, "never_ran")
    _write(conn, "archived_one")
    _record_run(conn, "ran_one", "2024-01-01 00:00:00")
    _record_run(conn, "archived_one", "2024-02-01 00:00:00")
    conn.execute("UPDATE script SET archived = 1 WHERE name = 'archived_one'")
    conn.commit()
    rows = recent_scripts(conn)
    assert [row["name"] for row in rows] == ["ran_one"]


def test_recent_scripts_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    result_limit = 2
    for i in range(4):
        _write(conn, f"script_{i}")
        _record_run(conn, f"script_{i}", f"2024-01-0{i + 1} 00:00:00")
    assert len(recent_scripts(conn, limit=result_limit)) == result_limit


def test_cmd_recent_prints_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "ran_one")
    _record_run(conn, "ran_one", "2024-01-01 00:00:00")
    conn.close()  # cmd_recent opens its own connection via GRIM_DB
    exit_code = cmd_recent(argparse.Namespace(limit=None))
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "ran_one" in out
    assert "last=2024-01-01 00:00:00" in out
