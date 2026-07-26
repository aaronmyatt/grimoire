"""Tests for verbs/update.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.update import UpdateRequest, update_script
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _seed_script(conn: sqlite3.Connection) -> None:
    write_script(
        conn,
        WriteRequest(
            name="foo_bar",
            language="python",
            description="d",
            body="print(1)",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def test_update_script_bumps_version_and_preserves_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    expected_version = 2
    result = update_script(conn, UpdateRequest(name="foo_bar", changelog="fix", body="print(2)"))
    assert result.version == expected_version
    v1 = conn.execute(
        "SELECT body FROM script_version WHERE script_id = ? AND version = 1", (result.script_id,)
    ).fetchone()
    assert v1["body"] == "print(1)"


def test_update_script_requires_changelog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    with pytest.raises(ValueError, match="changelog"):
        update_script(conn, UpdateRequest(name="foo_bar", changelog=" ", body="print(2)"))


def test_update_script_rejects_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        update_script(conn, UpdateRequest(name="does_not_exist", changelog="x", body="print(1)"))


def test_update_script_lints_against_existing_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    with pytest.raises(ValueError, match="syntax error"):
        update_script(
            conn, UpdateRequest(name="foo_bar", changelog="x", body="def broken(:\n pass")
        )
