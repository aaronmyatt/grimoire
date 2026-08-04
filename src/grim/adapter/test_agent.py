"""Tests for adapter/agent.py's strong_matches(). Self-contained per
verbs/CLAUDE.md-style slice isolation (root CLAUDE.md §7): seeds scripts via
raw SQL against the kernel schema, not by importing verbs/write.py.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.exceptions import UserInterruption
from minisweagent.models.test_models import DeterministicModel, make_output

from grim import db
from grim.adapter.agent import (
    RECALL_LIMIT_DEFAULT,
    RECALL_LIMIT_MAX,
    STRONG_MATCH_LIMIT,
    GrimAgent,
    rank_recall,
    recall_enabled,
    recall_limit,
    recent_library,
    strong_matches,
    user_prompt_extension,
)
from grim.adapter.environment import GrimEnvironment
from grim.adapter.tools import render_command


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


# --- library recall (--continue) --------------------------------------------


def _add_agent_session(conn: sqlite3.Connection, sid: str, when: str) -> None:
    conn.execute("INSERT INTO session (id, kind, started_at) VALUES (?, 'agent', ?)", (sid, when))
    conn.commit()


def _seed_recall_script(
    conn: sqlite3.Connection, name: str, *, seeded: int = 0, archived: int = 0
) -> None:
    cur = conn.execute(
        "INSERT INTO script (name, language, description, seeded, archived) "
        "VALUES (?, 'python', ?, ?, ?)",
        (name, f"{name} does a thing", seeded, archived),
    )
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, ?, ?)",
        (cur.lastrowid, "print(1)", f"hash-{name}"),
    )
    conn.commit()


class _Run(NamedTuple):
    name: str
    session: str
    seq: int
    when: str
    exit_code: int = 0


def _record_run(conn: sqlite3.Connection, run: _Run) -> None:
    vid = conn.execute(
        "SELECT v.id FROM script_version v JOIN script s ON s.id = v.script_id WHERE s.name = ?",
        (run.name,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (vid, run.session, run.seq, run.exit_code, run.when),
    )
    conn.commit()


def _candidate(name: str, runs: int, iterations: int, last_used: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} desc",
        "runs": runs,
        "iterations": iterations,
        "last_used": last_used,
    }


def test_rank_recall_selects_by_usage_and_orders_recent_last() -> None:
    cands = [
        _candidate("a", runs=10, iterations=1, last_used="2026-08-01 00:00:00"),
        _candidate("b", runs=5, iterations=3, last_used="2026-08-03 00:00:00"),
        _candidate("c", runs=1, iterations=1, last_used="2026-08-02 00:00:00"),
    ]
    out = rank_recall(cands, 2)  # keeps a (10 runs) and b (5 runs); drops c
    assert [m["name"] for m in out] == ["a", "b"]  # ordered by last_used ascending
    assert out[-1]["name"] == "b"  # most recently used lands LAST (recency slot)
    assert set(out[0]) == {"name", "description"}  # terse: no bodies or stats leak


def test_rank_recall_caps_at_k() -> None:
    cands = [
        _candidate(f"s{i}", runs=i, iterations=1, last_used=f"2026-08-0{i} 00:00:00")
        for i in range(1, 5)
    ]
    keep = 2
    assert len(rank_recall(cands, keep)) == keep


def test_recent_library_returns_recent_agent_scripts_most_recent_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_recall_script(conn, "compute_fib")
    _seed_recall_script(conn, "reverse_string")
    _add_agent_session(conn, "s1", "2026-08-01 09:00:00")
    _add_agent_session(conn, "s2", "2026-08-03 09:00:00")
    _record_run(conn, _Run("compute_fib", "s1", 0, "2026-08-01 09:00:00"))
    _record_run(conn, _Run("reverse_string", "s2", 0, "2026-08-03 09:00:00"))
    out = recent_library(RECALL_LIMIT_DEFAULT)
    assert [m["name"] for m in out] == ["compute_fib", "reverse_string"]  # recent last


def test_recent_library_excludes_seeded_archived_unrun_and_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _add_agent_session(conn, "s1", "2026-08-01 09:00:00")
    _seed_recall_script(conn, "seed_tool", seeded=1)  # a seed -> already in the prompt
    _seed_recall_script(conn, "old_tool", archived=1)  # archived
    _seed_recall_script(conn, "never_run")  # runs = 0
    _seed_recall_script(conn, "flaky_tool")  # ran, but failed
    _record_run(conn, _Run("seed_tool", "s1", 0, "2026-08-01 09:00:00"))
    _record_run(conn, _Run("old_tool", "s1", 1, "2026-08-01 09:01:00"))
    _record_run(conn, _Run("flaky_tool", "s1", 2, "2026-08-01 09:02:00", exit_code=1))
    assert recent_library(RECALL_LIMIT_DEFAULT) == []


def test_recall_limit_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_RECALL_LIMIT", raising=False)
    assert recall_limit() == RECALL_LIMIT_DEFAULT


def test_recall_limit_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = 3
    monkeypatch.setenv("GRIM_RECALL_LIMIT", str(chosen))
    assert recall_limit() == chosen


def test_recall_limit_clamps_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_RECALL_LIMIT", "100000")
    assert recall_limit() == RECALL_LIMIT_MAX


def test_recall_enabled_reflects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_RECALL", raising=False)
    assert recall_enabled() is False
    monkeypatch.setenv("GRIM_RECALL", "1")
    assert recall_enabled() is True


# --- /new and grim CLI-verb slash commands -----------------------------------


def _act(tool: str, args: dict[str, object]) -> dict[str, object]:
    """Shape an action exactly as GrimToolcallModel produces it, incl. the
    `command` display field InteractiveAgent requires (test_grimoire_e2e.py's
    identical helper)."""
    return {"tool": tool, "args": args, "tool_call_id": "c", "command": render_command(tool, args)}


def _grim_agent(outputs: list[dict[str, object]], session_id: str) -> GrimAgent:
    return GrimAgent(
        DeterministicModel(outputs=outputs),  # type: ignore[no-untyped-call]
        GrimEnvironment(session_id=session_id),
        system_template="You are a test agent.",
        instance_template="{{task}}",
        mode="yolo",
        cost_limit=0,
    )


def _scripted_prompt(responses: list[str]) -> Callable[..., str]:
    """Stand-in for InteractiveAgent._prompt_and_handle_slash_commands that
    returns each response in turn instead of reading real stdin."""
    queue: Iterator[str] = iter(responses)

    def _prompt(self: object, prompt: str, *, _multiline: bool = False) -> str:
        return next(queue)

    return _prompt


def test_prompt_dispatches_grim_verb_and_reprompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect()
    db.migrate(conn)
    _seed(conn, "read_file", "python", "print a file")
    agent = _grim_agent([], "sess-verb")
    monkeypatch.setattr(
        InteractiveAgent, "_prompt_and_handle_slash_commands", _scripted_prompt(["/list", "hello"])
    )

    result = agent._prompt_and_handle_slash_commands("> ")

    assert result == "hello"
    assert "read_file" in capsys.readouterr().out


def test_prompt_new_without_task_reprompts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = _grim_agent([], "sess-new-empty")
    monkeypatch.setattr(
        InteractiveAgent, "_prompt_and_handle_slash_commands", _scripted_prompt(["/new", "hi"])
    )

    result = agent._prompt_and_handle_slash_commands("> ")

    assert result == "hi"
    assert "usage: /new" in capsys.readouterr().out


def test_prompt_new_with_task_raises_new_session_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _grim_agent([], "sess-new")
    monkeypatch.setattr(
        InteractiveAgent,
        "_prompt_and_handle_slash_commands",
        _scripted_prompt(["/new do the thing"]),
    )

    with pytest.raises(UserInterruption) as exc_info:
        agent._prompt_and_handle_slash_commands("> ")

    message = exc_info.value.messages[0]
    assert message["role"] == "exit"
    assert message["extra"]["exit_status"] == "GrimNewSession"
    assert message["extra"]["submission"] == "do the thing"


def test_run_new_session_clears_history_and_rotates_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    db.migrate(db.connect())
    outputs = [
        make_output("finish 1", [_act("submit", {"result": "first done"})]),
        make_output("finish 2", [_act("submit", {"result": "second done"})]),
    ]
    env = GrimEnvironment(session_id="before-new")
    agent = GrimAgent(
        DeterministicModel(outputs=outputs),  # type: ignore[no-untyped-call]
        env,
        system_template="You are a test agent.",
        instance_template="{{task}}",
        mode="yolo",
        cost_limit=0,
    )
    # First "new task?" prompt (after submit #1) triggers /new; the second
    # (after submit #2, in the fresh sub-run) just presses Enter to finish.
    monkeypatch.setattr(
        InteractiveAgent,
        "_prompt_and_handle_slash_commands",
        _scripted_prompt(["/new second task", ""]),
    )

    result = agent.run("first task")

    assert result["exit_status"] == "Submitted"
    assert result["submission"] == "second done"
    assert env.session_id != "before-new"  # a fresh session_id was minted
    assert agent.messages[1]["content"] == "second task"  # history cleared, not appended
