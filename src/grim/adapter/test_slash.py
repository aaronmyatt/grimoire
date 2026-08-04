"""Tests for adapter/slash.py — `/verb args...` grim CLI mirror."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.adapter.slash import parse_slash_command, run_slash_command


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/find foo", ("find", ["foo"])),
        (
            "/write --name x --lang bash --desc d",
            ("write", ["--name", "x", "--lang", "bash", "--desc", "d"]),
        ),
        ('/find "disk space" --limit 3', ("find", ["disk space", "--limit", "3"])),
        ("/list", ("list", [])),
    ],
)
def test_parse_recognizes_grim_verbs(text: str, expected: tuple[str, list[str]]) -> None:
    assert parse_slash_command(text) == expected


@pytest.mark.parametrize(
    "text", ["/h", "/m", "/y", "/c", "/u", "/new do a thing", "just text", "", "/"]
)
def test_parse_rejects_non_grim_verb_input(text: str) -> None:
    assert parse_slash_command(text) is None


def test_parse_rejects_unbalanced_quotes() -> None:
    assert parse_slash_command("/find 'unterminated") is None


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect()
    db.migrate(conn)
    return conn


def _seed(conn: sqlite3.Connection, name: str, description: str) -> None:
    cur = conn.execute(
        "INSERT INTO script (name, language, description) VALUES (?, 'bash', ?)",
        (name, description),
    )
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, 'x', 'h')",
        (cur.lastrowid,),
    )
    conn.commit()


def test_run_slash_command_dispatches_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed(conn, "read_file", "print a file")

    output = run_slash_command("/list", "test-session")

    assert output is not None
    assert "read_file" in output


def test_run_slash_command_returns_none_for_non_grim_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_conn(tmp_path, monkeypatch)
    assert run_slash_command("/h", "test-session") is None
    assert run_slash_command("plain text", "test-session") is None
