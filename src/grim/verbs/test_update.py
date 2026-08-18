"""Tests for verbs/update.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.update import UpdateRequest, resolve_language, update_script
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


# --- resolve_language: pure, so tested without a database -------------------


def test_resolve_language_defaults_to_the_current_one() -> None:
    assert resolve_language(None, "bash") == "bash"


def test_resolve_language_accepts_an_enabled_override() -> None:
    # python/bash are the builtin pair, writable in every configuration.
    assert resolve_language("python", "bash") == "python"


def test_resolve_language_rejects_a_language_outside_the_writable_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # update must not be a side door around the gate write enforces.
    monkeypatch.setenv("GRIM_LANGUAGES", "")
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "python")
    with pytest.raises(ValueError, match="unsupported language"):
        resolve_language("bash", "python")


def test_resolve_language_rejects_a_language_outside_the_runner_catalog() -> None:
    with pytest.raises(ValueError, match="unsupported language|runner catalog"):
        resolve_language("brainfuck", "python")


# --- update --lang: the language actually changes ---------------------------


def test_update_can_rewrite_a_script_in_another_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The regression: a python body on a bash script used to fail the bash
    # lint with no way to override, so agents forked a near-duplicate *_py
    # sibling instead of rewriting in place.
    conn = _migrated_conn(tmp_path, monkeypatch)
    write_script(
        conn,
        WriteRequest(
            name="foo_bar",
            language="bash",
            description="d",
            body="echo 1",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )
    result = update_script(
        conn,
        UpdateRequest(
            name="foo_bar", changelog="port to python", body="print(1)", language="python"
        ),
    )
    assert result.language == "python"
    assert result.language_changed is True
    current = conn.execute(
        "SELECT language FROM script WHERE id = ?", (result.script_id,)
    ).fetchone()
    assert current["language"] == "python", "script.language tracks the current language"


def test_language_change_leaves_earlier_versions_dispatchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reason language lives on the version: resolving v1 after the change
    # must still say 'bash', or `grim run foo_bar@1` feeds a bash body to the
    # python runner.
    conn = _migrated_conn(tmp_path, monkeypatch)
    write_script(
        conn,
        WriteRequest(
            name="foo_bar",
            language="bash",
            description="d",
            body="echo 1",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )
    update_script(
        conn,
        UpdateRequest(name="foo_bar", changelog="port", body="print(1)", language="python"),
    )
    assert _shared.resolve_script_version(conn, "foo_bar", 1)["language"] == "bash"
    assert _shared.resolve_script_version(conn, "foo_bar", 2)["language"] == "python"
    assert _shared.resolve_script_version(conn, "foo_bar", None)["language"] == "python"


def test_update_without_lang_reports_no_change_and_lints_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    result = update_script(conn, UpdateRequest(name="foo_bar", changelog="fix", body="print(2)"))
    assert result.language == "python"
    assert result.language_changed is False


def test_update_lints_the_new_body_against_the_new_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Switching language does not switch off the lint — it retargets it.
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    with pytest.raises(ValueError, match="syntax error"):
        update_script(
            conn,
            UpdateRequest(name="foo_bar", changelog="x", body="if [ -z ; then\n", language="bash"),
        )
