"""Tests for adapter/agent.py's strong_matches(). Self-contained per
verbs/CLAUDE.md-style slice isolation (root CLAUDE.md §7): seeds scripts via
raw SQL against the kernel schema, not by importing verbs/write.py.
"""

from __future__ import annotations

import sqlite3
import subprocess
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
    _current_scope,
    rank_recall,
    recall_enabled,
    recall_limit,
    recent_library,
    seeded_roster,
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


def test_strong_matches_excludes_foreign_repo_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script written in another repo never reaches the system prompt,
    however strong its FTS score — that injection is exactly the cross-repo
    distraction the scope filter exists to stop."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_distractors(conn)
    _seed(
        conn=conn,
        name="extract_failing_tests",
        language="python",
        description="extracts failing pytest tests from a CI log",
    )
    conn.execute("UPDATE script SET scope = 'bbbbbbbbbbbb' WHERE name = 'extract_failing_tests'")
    conn.commit()
    assert strong_matches("extract failing pytest tests from ci logs") == []


def test_strong_matches_includes_current_repo_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_distractors(conn)
    repo = tmp_path / "wrk"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    scope = _current_scope()
    assert scope != "global"
    _seed(
        conn=conn,
        name="extract_failing_tests",
        language="python",
        description="extracts failing pytest tests from a CI log",
    )
    conn.execute("UPDATE script SET scope = ? WHERE name = 'extract_failing_tests'", (scope,))
    conn.commit()
    results = strong_matches("extract failing pytest tests from ci logs")
    assert [r["name"] for r in results] == ["extract_failing_tests"]


def test_current_scope_outside_git_repo_is_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _current_scope() == "global"


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


def _flag_seeded(conn: sqlite3.Connection, *names: str) -> None:
    for name in names:
        conn.execute("UPDATE script SET seeded = 1 WHERE name = ?", (name,))
    conn.commit()


def test_seeded_roster_lists_seeded_rows_in_seeding_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed(conn, "shell", "python", "run any shell command")
    _seed(conn, "read_file", "python", "read a file")
    _seed(conn, "my_own_tool", "python", "agent-authored, not a seed")
    _flag_seeded(conn, "shell", "read_file")

    roster = seeded_roster()

    assert roster == [
        {"name": "shell", "description": "run any shell command"},
        {"name": "read_file", "description": "read a file"},
    ]


def test_seeded_roster_drops_archived_and_taken_over_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt must never advertise a script run() would reject or the
    human pulled out of seed management — an eval arm seeded without
    `shell` (GRIM_BASE_SEEDS) relies on this to keep its control clean."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed(conn, "shell", "python", "run any shell command")
    _seed(conn, "stats", "python", "usage report")
    _flag_seeded(conn, "shell", "stats")
    conn.execute("UPDATE script SET archived = 1 WHERE name = 'shell'")
    conn.execute("UPDATE script SET seeded = 0 WHERE name = 'stats'")
    conn.commit()

    assert seeded_roster() == []


def test_seeded_roster_empty_library_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_conn(tmp_path, monkeypatch)

    assert seeded_roster() == []


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


def test_run_declares_the_environments_pinned_cwd_to_the_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """{{ grim_cwd }} renders the SAME value environment.py pins actions to
    (env.cwd) — declared and enforced from one source, so the prompt can
    never disagree with where scripts actually run."""
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    db.migrate(db.connect())
    env = GrimEnvironment(session_id="cwd-declared")
    agent = GrimAgent(
        DeterministicModel(outputs=[make_output("done", [_act("submit", {"result": "ok"})])]),  # type: ignore[no-untyped-call]
        env,
        system_template="wd: {{ grim_cwd }}",
        instance_template="{{task}}",
        mode="yolo",
        cost_limit=0,
    )
    monkeypatch.setattr(
        InteractiveAgent, "_prompt_and_handle_slash_commands", _scripted_prompt([""])
    )

    agent.run("task")

    assert agent.extra_template_vars["grim_cwd"] == env.cwd
    assert agent.messages[0]["content"] == f"wd: {env.cwd}"


# --- add_messages rich rendering (display.py) --------------------------------


def test_add_messages_renders_submit_as_markdown(capsys: pytest.CaptureFixture[str]) -> None:
    agent = _grim_agent([], "sess-render-submit")
    message = {
        "role": "assistant",
        "content": "Everything is in place.",
        "tool_calls": [{"function": {"name": "submit", "arguments": '{"result": "# Done"}'}}],
        "extra": {"actions": [_act("submit", {"result": "# Done\n\n- item one"})]},
    }

    agent.add_messages(message)

    out = capsys.readouterr().out
    assert "Everything is in place." in out
    assert "Done" in out
    assert "item one" in out
    assert '{"result"' not in out
    assert agent.messages[-1] is message, "history holds the verbatim dict, tool_calls intact"


def test_add_messages_renders_verb_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    agent = _grim_agent([], "sess-render-verb")
    message = {
        "role": "assistant",
        "content": None,
        "extra": {"actions": [_act("find", {"query": "dad jokes"})]},
    }

    agent.add_messages(message)

    out = capsys.readouterr().out
    assert "grim find 'dad jokes'" in out
    assert '{"query"' not in out


def test_add_messages_default_path_for_other_roles(capsys: pytest.CaptureFixture[str]) -> None:
    agent = _grim_agent([], "sess-render-tool")
    message = {"role": "tool", "content": "observation text"}

    agent.add_messages(message)

    out = capsys.readouterr().out
    assert "Tool" in out, "mini's default header still renders non-assistant roles"
    assert "observation text" in out
    assert agent.messages[-1] is message
