"""Smoke tests for db.py: fresh init, idempotent re-init, PRAGMAs, FTS5.

Build plan Phase 0 "done when": fresh init and re-init both succeed on a
temp DB — never the real ~/.grimoire/grimoire.db.
"""

from __future__ import annotations

from pathlib import Path

from grim import db


def test_fresh_migrate_applies_initial_schema(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "grimoire.db")
    applied = db.migrate(conn)
    assert applied == ["0001_initial.sql", "0002_tags.sql"]
    row = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    assert row[0] == len(applied)


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "grimoire.db"
    conn = db.connect(db_path)
    first_applied = db.migrate(conn)
    conn.close()

    conn2 = db.connect(db_path)
    applied_again = db.migrate(conn2)
    assert applied_again == []
    row = conn2.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    assert row[0] == len(first_applied)


def test_connect_sets_expected_pragmas(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "grimoire.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert str(mode).lower() == "wal"
    assert fk == 1


def test_script_fts_is_queryable(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "grimoire.db")
    db.migrate(conn)
    count = conn.execute("SELECT COUNT(*) FROM script_fts").fetchone()[0]
    assert count == 0


def test_init_db_returns_a_ready_connection(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "grimoire.db")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {"session", "script", "script_version", "execution"} <= tables
