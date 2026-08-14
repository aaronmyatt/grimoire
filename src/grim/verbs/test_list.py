"""Tests for verbs/list.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.list import ListFilters, list_scripts
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


_BODY_BY_LANGUAGE = {"python": "print(1)", "bash": "echo 1"}


def _write(
    conn: sqlite3.Connection, name: str, language: str = "python", scope: str = "global"
) -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language=language,
            description="d",
            body=_BODY_BY_LANGUAGE[language],
            parent=None,
            scope=scope,
            session_id="human-adhoc",
        ),
    )


def _record_run(conn: sqlite3.Connection, name: str, started_at: str) -> None:
    """Insert an execution row so script_health.last_used/runs reflect it —
    started_at is set explicitly so `--sort recent` has a deterministic order
    (no wall clock in tests, root CLAUDE.md §7)."""
    version = conn.execute(
        "SELECT sv.id FROM script_version sv JOIN script s ON s.id = sv.script_id "
        "WHERE s.name = ? ORDER BY sv.version DESC LIMIT 1",
        (name,),
    ).fetchone()
    _shared.ensure_session(conn, "human-adhoc")
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM execution WHERE session_id = 'human-adhoc'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) "
        "VALUES (?, 'human-adhoc', ?, 0, ?)",
        (version["id"], seq, started_at),
    )
    conn.commit()


def test_list_scripts_sort_recent_orders_by_last_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "old_one")
    _write(conn, "new_one")
    _record_run(conn, "old_one", "2020-01-01 00:00:00")
    _record_run(conn, "new_one", "2025-01-01 00:00:00")
    rows = list_scripts(
        conn, ListFilters(scope=None, language=None, limit=20, offset=0, sort="recent")
    )
    # most-recently-run first; never-run scripts (last_used NULL) sort last.
    assert [row["name"] for row in rows] == ["new_one", "old_one"]


def test_list_scripts_sort_recent_puts_never_run_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "ran_once")
    _write(conn, "never_ran")
    _record_run(conn, "ran_once", "2024-06-01 00:00:00")
    rows = list_scripts(
        conn, ListFilters(scope=None, language=None, limit=20, offset=0, sort="recent")
    )
    assert [row["name"] for row in rows] == ["ran_once", "never_ran"]


def test_list_scripts_sort_runs_orders_by_run_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "busy_one")
    _write(conn, "quiet_one")
    _record_run(conn, "busy_one", "2024-01-01 00:00:00")
    _record_run(conn, "busy_one", "2024-01-02 00:00:00")
    _record_run(conn, "quiet_one", "2024-01-03 00:00:00")
    rows = list_scripts(
        conn, ListFilters(scope=None, language=None, limit=20, offset=0, sort="runs")
    )
    assert [row["name"] for row in rows] == ["busy_one", "quiet_one"]


def test_list_scripts_returns_all_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "foo_bar")
    _write(conn, "baz_qux")
    rows = list_scripts(conn, ListFilters(scope=None, language=None, limit=20, offset=0))
    assert {row["name"] for row in rows} == {"foo_bar", "baz_qux"}


def test_list_scripts_filters_by_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "foo_bar", language="python")
    _write(conn, "baz_qux", language="bash")
    rows = list_scripts(conn, ListFilters(scope=None, language="bash", limit=20, offset=0))
    assert [row["name"] for row in rows] == ["baz_qux"]


def test_list_scripts_filters_by_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "foo_bar", scope="global")
    _write(conn, "baz_qux", scope="abc123abc123")
    rows = list_scripts(conn, ListFilters(scope="abc123abc123", language=None, limit=20, offset=0))
    assert [row["name"] for row in rows] == ["baz_qux"]


def test_list_scripts_respects_limit_and_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    page_size = 2
    for i in range(5):
        _write(conn, f"script_{i}")
    rows = list_scripts(
        conn, ListFilters(scope=None, language=None, limit=page_size, offset=page_size)
    )
    assert len(rows) == page_size


def test_list_scripts_excludes_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "foo_bar")
    conn.execute("UPDATE script SET archived = 1 WHERE name = 'foo_bar'")
    conn.commit()
    assert list_scripts(conn, ListFilters(scope=None, language=None, limit=20, offset=0)) == []
