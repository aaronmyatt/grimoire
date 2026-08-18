"""Smoke tests for db.py: fresh init, idempotent re-init, PRAGMAs, FTS5.

Build plan Phase 0 "done when": fresh init and re-init both succeed on a
temp DB — never the real ~/.grimoire/grimoire.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db


def test_fresh_migrate_applies_every_bundled_migration(tmp_path: Path) -> None:
    # Derived from the directory, not a hardcoded list: adding a migration is
    # a routine schema change and must not require editing this assertion —
    # and `migrations/` is a different slice from this file, so the fence
    # cannot land a new .sql and its list edit in one change set anyway.
    bundled = sorted(p.name for p in db.MIGRATIONS_DIR.glob("*.sql"))
    assert bundled[0] == "0001_initial.sql", "0001 is the schema floor, never renamed"

    conn = db.connect(tmp_path / "grimoire.db")
    applied = db.migrate(conn)

    assert applied == bundled, "a fresh db applies every bundled migration, in filename order"
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


def test_0003_backfills_version_language_from_the_script(tmp_path: Path) -> None:
    # Upgrade path, not the fresh-install path: a database written before
    # 0003 has versions but no per-version language. The backfill must give
    # every one of them its script's language, because that is provably what
    # each body was written in (update.py linted against it) — a NULL left
    # behind would later resolve through COALESCE to the script's CURRENT
    # language and dispatch an old body to the wrong interpreter.
    conn = sqlite3.connect(tmp_path / "grimoire.db")
    pre_0003 = ["0001_initial.sql", "0002_tags.sql"]
    for name in pre_0003:
        conn.executescript((db.MIGRATIONS_DIR / name).read_text())
    for script_id, (name, language) in enumerate([("old_bash", "bash"), ("old_py", "python")], 1):
        conn.execute(
            "INSERT INTO script (name, language, description) VALUES (?, ?, 'd')",
            (name, language),
        )
        for version in (1, 2):
            conn.execute(
                "INSERT INTO script_version (script_id, version, body, body_hash) "
                "VALUES (?, ?, 'body', 'hash')",
                (script_id, version),
            )
    conn.commit()

    conn.executescript((db.MIGRATIONS_DIR / "0003_version_language.sql").read_text())

    rows = conn.execute(
        "SELECT s.name, sv.language FROM script_version sv "
        "JOIN script s ON s.id = sv.script_id ORDER BY s.name, sv.version"
    ).fetchall()
    assert [r[1] for r in rows] == ["bash", "bash", "python", "python"]
    unfilled = conn.execute("SELECT COUNT(*) FROM script_version WHERE language IS NULL").fetchone()
    assert unfilled[0] == 0, "the backfill must leave no version without a language"


def test_connect_bounds_single_value_length(tmp_path: Path) -> None:
    # Defense in depth behind verbs/run.py's stream clamp: an oversized
    # value (an unclamped buffer upstream) fails at a boundary we chose,
    # not at SQLite's ~1 GB compile-time SQLITE_MAX_LENGTH — the accident
    # line the 2026-08-15 runaway-stdout session crash found.
    conn = db.connect(tmp_path / "grimoire.db")
    assert conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == db.MAX_VALUE_LENGTH_BYTES

    conn.execute("CREATE TABLE limit_probe (v TEXT)")
    with pytest.raises(sqlite3.DataError, match="string or blob too big"):
        conn.execute("INSERT INTO limit_probe VALUES (?)", ("x" * (db.MAX_VALUE_LENGTH_BYTES + 1),))


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
