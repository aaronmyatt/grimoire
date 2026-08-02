"""Tests for adapter/agent.py's strong_matches(). Self-contained per
verbs/CLAUDE.md-style slice isolation (root CLAUDE.md §7): seeds scripts via
raw SQL against the kernel schema, not by importing verbs/write.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.adapter.agent import STRONG_MATCH_LIMIT, strong_matches, user_prompt_extension


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect()
    db.migrate(conn)
    return conn


def _seed(conn: sqlite3.Connection, name: str, language: str, description: str) -> None:
    cursor = conn.execute(
        "INSERT INTO script (name, language, description) VALUES (?, ?, ?)",
        (name, language, description),
    )
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, ?, ?)",
        (cursor.lastrowid, "print(1)", "deadbeef"),
    )
    conn.commit()


def _seed_distractors(conn: sqlite3.Connection) -> None:
    """A handful of unrelated scripts, so bm25's IDF term reflects a real
    multi-document corpus instead of degenerating toward zero — with only
    one document, every query token appears in 100% of the corpus and
    every score collapses near 0 regardless of match quality."""
    _seed(conn, "fetch_dad_joke", "bash", "fetches a random dad joke from an API")
    _seed(conn, "list_github_repos", "bash", "lists public GitHub repos for a username")
    _seed(conn, "gardener", "python", "proposes archive candidates for duplicate scripts")


def test_strong_matches_finds_close_name_and_description_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_distractors(conn)
    _seed(
        conn=conn,
        name="extract_failing_tests",
        language="python",
        description="extracts failing pytest tests from a CI log",
    )
    results = strong_matches("extract failing pytest tests from ci logs")
    assert results
    assert results[0]["name"] == "extract_failing_tests"


def test_strong_matches_excludes_weak_or_unrelated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_distractors(conn)
    _seed(
        conn=conn,
        name="extract_failing_tests",
        language="python",
        description="extracts failing pytest tests from a CI log",
    )
    assert strong_matches("draw me an epic SDLC diagram in excalidraw") == []


def test_strong_matches_empty_task_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_conn(tmp_path, monkeypatch)
    assert strong_matches("") == []


def test_strong_matches_never_exceeds_its_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_distractors(conn)
    for i in range(STRONG_MATCH_LIMIT + 2):
        _seed(
            conn=conn,
            name=f"extract_failing_tests_{i}",
            language="python",
            description="extracts failing pytest tests from a CI log",
        )
    results = strong_matches("extract failing pytest tests from ci logs")
    assert len(results) <= STRONG_MATCH_LIMIT


def test_user_prompt_extension_reads_and_strips_the_file(tmp_path: Path) -> None:
    p = tmp_path / "system.md"
    p.write_text("\n  Prefer bash over python for one-offs.\n\n")
    assert user_prompt_extension(p) == "Prefer bash over python for one-offs."


def test_user_prompt_extension_missing_file_returns_empty(tmp_path: Path) -> None:
    assert user_prompt_extension(tmp_path / "absent.md") == ""


def test_user_prompt_extension_blank_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "system.md"
    p.write_text("   \n\t\n")
    assert user_prompt_extension(p) == ""  # renders nothing under the yaml guard
