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
    _write(conn, "baz_qux", scope="repo:abc123")
    rows = list_scripts(conn, ListFilters(scope="repo:abc123", language=None, limit=20, offset=0))
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
