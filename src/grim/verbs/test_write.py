"""Tests for verbs/write.py."""

from __future__ import annotations

import argparse
import io
import sqlite3
import subprocess
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.write import WriteRequest, cmd_write, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _request(**overrides: object) -> WriteRequest:
    fields: dict[str, object] = {
        "name": "extract_failing_tests",
        "language": "python",
        "description": "extracts failing pytest tests",
        "body": "print('hi')",
        "parent": None,
        "scope": "global",
        "session_id": "human-adhoc",
    }
    fields.update(overrides)
    return WriteRequest(**fields)  # type: ignore[arg-type]


def test_write_script_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    result = write_script(conn, _request())
    assert result.version == 1
    row = conn.execute("SELECT * FROM script WHERE id = ?", (result.script_id,)).fetchone()
    assert row["name"] == "extract_failing_tests"
    assert row["scope"] == "global"


def _script_tags(conn: sqlite3.Connection, script_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT t.name FROM tag t JOIN script_tag st ON st.tag_id = t.id WHERE st.script_id = ?",
        (script_id,),
    ).fetchall()
    return [row["name"] for row in rows]


def test_write_script_repo_scope_stamps_provenance_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scope='repo' (the tool-schema literal) resolves to the repo's 12-hex
    root-commit id, and the human-readable name lands as a repo-<name> tag."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    conn = _migrated_conn(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    result = write_script(conn, _request(scope="repo"))
    row = conn.execute("SELECT scope FROM script WHERE id = ?", (result.script_id,)).fetchone()
    assert row["scope"] != "global"
    assert _shared.SCOPE_RE.match(row["scope"])
    assert _script_tags(conn, result.script_id) == ["repo-myrepo"]


def test_write_script_global_scope_writes_no_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    result = write_script(conn, _request(scope="global"))
    assert _script_tags(conn, result.script_id) == []


def test_write_script_rejects_legacy_scope_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="invalid scope"):
        write_script(conn, _request(scope="repo:abc123def456"))


def test_write_script_rejects_duplicate_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    write_script(conn, _request())
    with pytest.raises(ValueError, match="already exists"):
        write_script(conn, _request())


def test_write_script_rejects_blank_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="description"):
        write_script(conn, _request(description="   "))


def test_write_script_rejects_invalid_python_with_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="syntax error"):
        write_script(conn, _request(name="broken_script", body="def broken(:\n    pass"))


def test_write_script_rejects_unsupported_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unsupported language"):
        write_script(conn, _request(language="javascript"))


def test_write_script_fork_sets_parent_version_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    parent = write_script(conn, _request(name="parent_script"))
    write_script(conn, _request(name="child_script", parent="parent_script"))
    row = conn.execute(
        "SELECT parent_version_id FROM script WHERE name = 'child_script'"
    ).fetchone()
    assert row["parent_version_id"] == parent.version_id


def test_write_script_similarity_nudge_surfaces_existing_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    write_script(conn, _request())
    result = write_script(
        conn, _request(name="extract_failing_pytest", description="extracts failing pytest results")
    )
    assert any(name == "extract_failing_tests" for name, _score in result.similar)


def test_cmd_write_prints_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    monkeypatch.setattr("sys.stdin", io.StringIO("print('hi')"))
    args = argparse.Namespace(name="foo_bar", lang="python", desc="d", parent=None, scope="global")

    exit_code = cmd_write(args)

    assert exit_code == 0
    assert "wrote foo_bar@1" in capsys.readouterr().out


def test_write_script_accepts_enabled_extended_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    monkeypatch.setenv("GRIM_LANGUAGES", "ruby")

    result = write_script(conn, _request(name="ruby_greet", language="ruby", body="puts 'hi'"))

    assert result.version == 1
    row = conn.execute("SELECT language FROM script WHERE id = ?", (result.script_id,)).fetchone()
    assert row["language"] == "ruby"


def test_write_script_rejects_disabled_extended_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    monkeypatch.delenv("GRIM_LANGUAGES", raising=False)

    with pytest.raises(ValueError, match="unsupported language"):
        write_script(conn, _request(language="ruby", body="puts 'hi'"))


def test_write_script_ungated_bypasses_writable_set_but_not_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the silent seed drop: seeding writes with the gate off
    must land even when the env toggles exclude the seed's language."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    monkeypatch.delenv("GRIM_LANGUAGES", raising=False)  # ruby not writable
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "")  # builtins removed too

    result = write_script(
        conn,
        _request(name="ruby_seed", language="ruby", body="puts 'hi'"),
        enforce_language_gate=False,
    )

    assert result.version == 1
    row = conn.execute("SELECT language FROM script WHERE id = ?", (result.script_id,)).fetchone()
    assert row["language"] == "ruby"


def test_write_script_ungated_still_rejects_unknown_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="runner catalog"):
        write_script(conn, _request(language="javascript"), enforce_language_gate=False)
