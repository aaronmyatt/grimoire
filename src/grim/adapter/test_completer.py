"""Tests for adapter/completer.py — @/: completion over the script library and
files. Seeds scripts via raw SQL (slice isolation, root CLAUDE.md §7)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("prompt_toolkit")  # ships with the `agent` extra

from prompt_toolkit.completion import CompleteEvent, Completion  # noqa: E402
from prompt_toolkit.document import Document  # noqa: E402

from grim import db  # noqa: E402
from grim.adapter.completer import GrimCompleter  # noqa: E402


def _migrated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
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


def _complete(text: str) -> list[Completion]:
    doc = Document(text=text, cursor_position=len(text))
    return list(GrimCompleter().get_completions(doc, CompleteEvent()))


def test_colon_completes_script_names_by_token_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    _seed(conn, "read_file", "print stuff")
    _seed(conn, "run_bg", "start a job")

    texts = [c.text for c in _complete("please :re")]

    assert "read_file" in texts
    assert "run_bg" not in texts  # no token of name or description starts with 're'


def test_colon_fuzzy_matches_beyond_the_name_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    _seed(conn, "run_metrics", "collect eval numbers")
    _seed(conn, "wal_copy", "read a checkpointed database safely")

    # 'metr' is not a prefix of the whole name — FTS reaches the second token.
    assert [c.text for c in _complete(":metr")] == ["run_metrics"]
    # 'database' appears only in the description — same index `grim find` uses.
    assert [c.text for c in _complete(":database")] == ["wal_copy"]


def test_colon_matches_are_uncapped_and_bare_colon_lists_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    script_count = 25  # comfortably above the old 10-script cap
    for i in range(script_count):
        _seed(conn, f"note_{i:02}", "a note taker")

    assert len(_complete(":note")) == script_count
    assert len(_complete("look :")) == script_count  # bare ':' lists the library


def test_colon_is_scripts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    _seed(conn, "read_file", "print a file")

    completions = _complete(":rea")

    assert [c.text for c in completions] == ["read_file"]
    assert all(c.display_meta_text.startswith("script") for c in completions)


def test_at_is_files_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    _seed(conn, "READ_script", "would match the prefix if scripts were included")
    (tmp_path / "README.md").write_text("hi")
    monkeypatch.chdir(tmp_path)

    # PathCompleter's .text is the insert-remainder ("ME.md" after "READ");
    # the full filename is in .display_text.
    completions = _complete("see @READ")
    metas = {c.display_text: c.display_meta_text for c in completions}

    assert "README.md" in metas
    assert metas["README.md"] == "file"
    assert all(c.display_meta_text == "file" for c in completions)  # no scripts under `@`


def test_at_fuzzy_matches_non_prefix_and_across_dots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run_metrics.py").write_text("hi")
    monkeypatch.chdir(tmp_path)

    # 'metr' is not a prefix and 'mtr.py' spans the extension dot — both are
    # subsequences of the filename, which is all fuzzy matching requires.
    assert "run_metrics.py" in [c.display_text for c in _complete("@metr")]
    assert "run_metrics.py" in [c.display_text for c in _complete("@mtr.py")]


def test_at_file_matches_are_uncapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_count = 25  # comfortably above the old 10-file cap
    for i in range(file_count):
        (tmp_path / f"note_{i:02}.md").write_text("hi")
    monkeypatch.chdir(tmp_path)

    completions = _complete("@note")

    assert len(completions) == file_count


def test_plain_text_yields_no_completions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_db(tmp_path, monkeypatch)
    _seed(conn, "read_file", "print a file")

    assert _complete("just a normal sentence") == []


def test_completion_survives_an_unmigrated_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "absent.db"))  # never created

    assert _complete(":re") == []  # no crash, just nothing


def test_install_attaches_completer_to_all_mini_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("minisweagent")
    from minisweagent.agents.utils import prompt_user

    from grim.adapter.completer import install_grim_completer

    # grim-agent self-heals the submit-on-Enter patch into the venv; against a
    # pristine minisweagent, synthesize the session so this branch is covered.
    if not hasattr(prompt_user, "_task_prompt_session"):
        monkeypatch.setattr(
            prompt_user, "_task_prompt_session", prompt_user._multiline_prompt_session
        )

    install_grim_completer()

    assert isinstance(prompt_user.prompt_session.completer, GrimCompleter)
    assert isinstance(prompt_user._multiline_prompt_session.completer, GrimCompleter)
    assert isinstance(prompt_user._task_prompt_session.completer, GrimCompleter)
