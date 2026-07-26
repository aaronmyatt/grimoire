"""Tests for verbs/_shared.py's internal helpers."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


_FIXTURE_NAME = "extract_failing_tests"
_FIXTURE_LANGUAGE = "python"
_FIXTURE_DESCRIPTION = "extracts failing pytest tests"
_FIXTURE_BODY = "print('hi')"


def _insert_script(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO script (name, language, description) VALUES (?, ?, ?)",
        (_FIXTURE_NAME, _FIXTURE_LANGUAGE, _FIXTURE_DESCRIPTION),
    )
    script_id = int(
        conn.execute("SELECT id FROM script WHERE name = ?", (_FIXTURE_NAME,)).fetchone()["id"]
    )
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, ?, ?)",
        (script_id, _FIXTURE_BODY, _shared.body_hash(_FIXTURE_BODY)),
    )
    conn.commit()
    return script_id


def test_validate_slug_accepts_valid_names() -> None:
    _shared.validate_slug("extract_failing_tests")


def test_validate_slug_rejects_invalid_names() -> None:
    with pytest.raises(ValueError, match="invalid script name"):
        _shared.validate_slug("Not-Valid!")


def test_lint_accepts_valid_python() -> None:
    assert _shared.lint("python", "print('hi')") is None


def test_lint_rejects_invalid_python() -> None:
    error = _shared.lint("python", "def broken(:\n    pass")
    assert error is not None
    assert "syntax error" in error


def test_lint_bash_accepts_valid_and_rejects_invalid() -> None:
    assert _shared.lint("bash", "echo hi") is None
    error = _shared.lint("bash", "if [ 1 -eq 1")
    assert error is not None
    assert "syntax error" in error


def test_lint_unknown_language_passes_through() -> None:
    assert _shared.lint("ruby", "this is not even ruby {{{") is None


def test_body_hash_is_deterministic() -> None:
    assert _shared.body_hash("hello") == _shared.body_hash("hello")
    assert _shared.body_hash("hello") != _shared.body_hash("world")


def test_parse_name_version_bare_name() -> None:
    assert _shared.parse_name_version("extract_tests") == ("extract_tests", None)


def test_parse_name_version_pinned() -> None:
    assert _shared.parse_name_version("extract_tests@3") == ("extract_tests", 3)


def test_resolve_script_version_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_script(conn)
    row = _shared.resolve_script_version(conn, "extract_failing_tests", None)
    assert row["version"] == 1
    assert row["language"] == "python"


def test_resolve_script_version_missing_name_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        _shared.resolve_script_version(conn, "does_not_exist", None)


def test_resolve_script_version_missing_version_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_script(conn)
    with pytest.raises(LookupError):
        _shared.resolve_script_version(conn, "extract_failing_tests", 2)


def test_ensure_session_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _shared.ensure_session(conn, "human-adhoc")
    _shared.ensure_session(conn, "human-adhoc")
    conn.commit()
    row = conn.execute("SELECT COUNT(*) AS n FROM session WHERE id = 'human-adhoc'").fetchone()
    assert row["n"] == 1


def test_ensure_session_kind_for_agent_vs_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _shared.ensure_session(conn, "human-adhoc")
    _shared.ensure_session(conn, "sess-123")
    conn.commit()
    kinds = {row["id"]: row["kind"] for row in conn.execute("SELECT id, kind FROM session")}
    assert kinds["human-adhoc"] == "human"
    assert kinds["sess-123"] == "agent"


def test_default_scope_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _shared.default_scope() == "global"


def test_default_scope_inside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert _shared.default_scope().startswith("repo:")


def test_similar_scripts_finds_matching_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_script(conn)
    results = _shared.similar_scripts(conn, "failing pytest")
    assert results
    assert results[0][0] == "extract_failing_tests"


def test_similar_scripts_no_match_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_script(conn)
    results = _shared.similar_scripts(conn, "completely unrelated gibberish zzz")
    assert results == []


def test_session_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_SESSION", raising=False)
    assert _shared.session_id_from_env() == "human-adhoc"
    monkeypatch.setenv("GRIM_SESSION", "sess-abc")
    assert _shared.session_id_from_env() == "sess-abc"
