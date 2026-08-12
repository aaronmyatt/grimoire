"""Tests for verbs/find.py."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.find import cmd_find, find_scripts
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _write(conn: sqlite3.Connection, name: str, description: str) -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description=description,
            body="print(1)",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def test_find_scripts_ranks_name_and_description_over_body_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "extract_failing_tests", "extracts failing pytest tests")
    _write(conn, "unrelated_script", "does something else entirely")
    results = find_scripts(conn, "extract failing tests")
    assert results
    assert results[0]["name"] == "extract_failing_tests"


def test_find_scripts_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    result_limit = 2
    for i in range(3):
        _write(conn, f"failing_test_helper_{i}", "handles failing tests")
    results = find_scripts(conn, "failing tests", limit=result_limit)
    assert len(results) == result_limit


def test_find_scripts_excludes_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "extract_failing_tests", "extracts failing pytest tests")
    conn.execute("UPDATE script SET archived = 1 WHERE name = 'extract_failing_tests'")
    conn.commit()
    assert find_scripts(conn, "extract failing tests") == []


def test_find_scripts_no_match_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "extract_failing_tests", "extracts failing pytest tests")
    assert find_scripts(conn, "completely unrelated gibberish zzz") == []


def test_find_scripts_includes_seeded_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeds are ordinary library citizens: find must never grow a seed
    filter (the adapter's recall exclusion is deliberate; this is not)."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "apply_patch", "patch or edit a file by applying a unified diff")
    conn.execute("UPDATE script SET seeded = 1 WHERE name = 'apply_patch'")
    conn.commit()
    results = find_scripts(conn, "patch a file")
    assert [r["name"] for r in results] == ["apply_patch"]


def test_cmd_find_shows_last_used_dash_for_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch).close()  # GRIM_DB set; cmd_find opens its own conn
    conn = _shared.connect()
    _write(conn, "extract_failing_tests", "extracts failing pytest tests")
    exit_code = cmd_find(argparse.Namespace(query="extract failing tests", limit=None))
    out = capsys.readouterr().out
    assert exit_code == 0
    # never-run script -> "last=-" placeholder, not "last=None".
    assert "last=-" in out
