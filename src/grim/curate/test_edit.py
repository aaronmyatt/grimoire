"""Tests for curate/edit.py."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pytest

from grim import db
from grim.curate import _shared
from grim.curate.edit import (
    ChangelogOptions,
    PersistRequest,
    ai_changelog,
    changelog_model,
    cmd_edit,
    edit_in_editor,
    persist_edit,
    resolve_changelog,
    unified_diff,
)
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _write(conn: sqlite3.Connection, name: str, body: str = "print(1)\n") -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description="d",
            body=body,
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def _echo_editor(text: str) -> str:
    """A real, deterministic subprocess standing in for $EDITOR: overwrites
    the temp file with `text` instead of opening a real interactive editor."""
    script = f"import pathlib,sys; pathlib.Path(sys.argv[1]).write_text({text!r})"
    return f"{sys.executable} -c {script!r}"


# --- edit_in_editor -----------------------------------------------------


def test_edit_in_editor_round_trips_through_a_real_subprocess() -> None:
    result = edit_in_editor("original\n", "python", editor=_echo_editor("changed\n"))
    assert result == "changed\n"


def test_edit_in_editor_returns_content_even_if_editor_exits_nonzero() -> None:
    script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('x'); sys.exit(1)"
    editor = f"{sys.executable} -c {script!r}"
    assert edit_in_editor("orig", "bash", editor=editor) == "x"


# --- unified_diff ---------------------------------------------------------


def test_unified_diff_is_empty_for_identical_text() -> None:
    assert unified_diff("same\n", "same\n") == ""


def test_unified_diff_shows_the_change() -> None:
    diff = unified_diff("a\n", "b\n")
    assert "-a" in diff
    assert "+b" in diff


# --- ai_changelog -----------------------------------------------------------


def test_ai_changelog_returns_none_without_a_model() -> None:
    assert ai_changelog("some diff", None) is None


def test_ai_changelog_returns_none_on_blank_diff() -> None:
    assert ai_changelog("   ", "gpt-4o-mini") is None


def test_ai_changelog_uses_the_injected_complete_fn() -> None:
    result = ai_changelog("diff", "gpt-4o-mini", complete=lambda diff, model: "  fix the bug  \n")
    assert result == "fix the bug"


def test_ai_changelog_degrades_on_any_failure() -> None:
    def _boom(diff: str, model: str) -> str:
        raise RuntimeError("network down")

    assert ai_changelog("diff", "gpt-4o-mini", complete=_boom) is None


# --- changelog_model --------------------------------------------------------


def test_changelog_model_prefers_its_own_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_CHANGELOG_MODEL", "cheap-model")
    monkeypatch.setenv("GRIM_MODEL", "big-model")
    assert changelog_model() == "cheap-model"


def test_changelog_model_falls_back_to_grim_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_CHANGELOG_MODEL", raising=False)
    monkeypatch.setenv("GRIM_MODEL", "big-model")
    assert changelog_model() == "big-model"


def test_changelog_model_none_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_CHANGELOG_MODEL", raising=False)
    monkeypatch.delenv("GRIM_MODEL", raising=False)
    assert changelog_model() is None


# --- resolve_changelog -------------------------------------------------------


def test_resolve_changelog_prefers_the_override() -> None:
    options = ChangelogOptions(override="manual note", model=None, ai_fn=lambda d, m: "ai note")
    assert resolve_changelog("diff", options) == "manual note"


def test_resolve_changelog_uses_ai_when_no_override() -> None:
    options = ChangelogOptions(override=None, model="m", ai_fn=lambda d, m: "ai note")
    assert resolve_changelog("diff", options) == "ai note"


def test_resolve_changelog_prompts_when_ai_returns_none() -> None:
    options = ChangelogOptions(
        override=None, model=None, ai_fn=lambda d, m: None, prompt_fn=lambda p: "typed note"
    )
    assert resolve_changelog("diff", options) == "typed note"


def test_resolve_changelog_falls_back_to_generic_on_eof() -> None:
    def _raise(prompt: str) -> str:
        raise EOFError

    options = ChangelogOptions(override=None, model=None, ai_fn=lambda d, m: None, prompt_fn=_raise)
    assert resolve_changelog("diff", options) == "edited via grim edit"


# --- persist_edit -------------------------------------------------------


def test_persist_edit_appends_a_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    row = _shared.resolve_script_version(conn, "greet", None)

    result = persist_edit(
        conn,
        PersistRequest(
            script_id=row["script_id"],
            latest_version=row["version"],
            language="python",
            body="print(2)\n",
            changelog="bumped the output",
        ),
    )

    assert result.version == row["version"] + 1
    latest = _shared.resolve_script_version(conn, "greet", None)
    assert latest["body"] == "print(2)\n"
    assert latest["changelog"] == "bumped the output"


def test_persist_edit_rejects_bad_syntax(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet")
    row = _shared.resolve_script_version(conn, "greet", None)

    with pytest.raises(ValueError, match="syntax error"):
        persist_edit(
            conn,
            PersistRequest(
                script_id=row["script_id"],
                latest_version=row["version"],
                language="python",
                body="def broken(:\n",
                changelog="oops",
            ),
        )


# --- cmd_edit -------------------------------------------------------


def test_cmd_edit_unknown_name_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch).close()
    monkeypatch.setenv("EDITOR", _echo_editor("unused"))

    exit_code = cmd_edit(argparse.Namespace(name="ghost", changelog=None))

    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_edit_no_changes_is_a_clean_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet", "print(1)\n")
    conn.close()
    monkeypatch.setenv("EDITOR", _echo_editor("print(1)\n"))  # editor "changes" nothing

    exit_code = cmd_edit(argparse.Namespace(name="greet", changelog=None))

    assert exit_code == 0
    assert "no changes" in capsys.readouterr().out


def test_cmd_edit_persists_with_the_changelog_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet", "print(1)\n")
    monkeypatch.setenv("EDITOR", _echo_editor("print(2)\n"))

    exit_code = cmd_edit(argparse.Namespace(name="greet", changelog="bumped it"))

    assert exit_code == 0
    assert "bumped it" in capsys.readouterr().out
    latest = _shared.resolve_script_version(conn, "greet", None)
    assert latest["body"] == "print(2)\n"
    assert latest["changelog"] == "bumped it"


def test_cmd_edit_degrades_to_generic_changelog_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No --changelog, no model configured, and pytest's captured stdin makes
    # input() raise OSError — resolve_changelog must still complete cleanly.
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "greet", "print(1)\n")
    monkeypatch.delenv("GRIM_CHANGELOG_MODEL", raising=False)
    monkeypatch.delenv("GRIM_MODEL", raising=False)
    monkeypatch.setenv("EDITOR", _echo_editor("print(2)\n"))

    exit_code = cmd_edit(argparse.Namespace(name="greet", changelog=None))

    assert exit_code == 0
    latest = _shared.resolve_script_version(conn, "greet", None)
    assert latest["changelog"] == "edited via grim edit"
