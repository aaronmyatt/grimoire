"""Tests for verbs/read.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.read import read_execution_page, read_script
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _seed_script(conn: sqlite3.Connection) -> int:
    result = write_script(
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
    return result.version_id


def _seed_execution(conn: sqlite3.Connection, script_version_id: int, stdout: str) -> int:
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES ('human-adhoc', 'human')")
    cursor = conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code, stdout, stderr) "
        "VALUES (?, 'human-adhoc', 1, 0, ?, '')",
        (script_version_id, stdout),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def test_read_script_resolves_latest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    result = read_script(conn, "foo_bar", None)
    assert result.version == 1
    assert result.body == "print(1)"


def test_read_script_resolves_pinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    assert read_script(conn, "foo_bar", 1).version == 1


def test_read_script_raises_on_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        read_script(conn, "does_not_exist", None)


def test_read_execution_page_returns_first_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    version_id = _seed_script(conn)
    exec_id = _seed_execution(conn, version_id, "line1\nline2")
    page = read_execution_page(conn, exec_id, 1)
    assert "line1" in page
    assert "page 1/1" in page


def test_read_execution_page_paginates_long_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    version_id = _seed_script(conn)
    lines = "\n".join(f"line{i}" for i in range(1, 250))
    exec_id = _seed_execution(conn, version_id, lines)
    page1 = read_execution_page(conn, exec_id, 1)
    page2 = read_execution_page(conn, exec_id, 2)
    assert "line200" in page1
    assert "line201" not in page1
    assert "line201" in page2
    assert "page 2/2" in page2


def test_read_execution_page_raises_on_unknown_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        read_execution_page(conn, 999, 1)
