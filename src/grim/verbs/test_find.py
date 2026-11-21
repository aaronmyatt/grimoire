"""Tests for verbs/find.py."""

from __future__ import annotations

import argparse
import re
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


def _write(conn: sqlite3.Connection, name: str, description: str, scope: str = "global") -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description=description,
            body="print(1)",
            parent=None,
            scope=scope,
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


def test_find_scripts_tiers_current_repo_before_global_before_foreign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equal FTS relevance: provenance decides — current repo, then global,
    then other repos / legacy scopes."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    current_scope = "aaaaaaaaaaaa"
    _write(conn, "helper_foreign", "handles failing tests", scope="bbbbbbbbbbbb")
    _write(conn, "helper_global", "handles failing tests", scope="global")
    _write(conn, "helper_current", "handles failing tests", scope=current_scope)
    results = find_scripts(conn, "failing tests", scope=current_scope)
    assert [r["name"] for r in results] == ["helper_current", "helper_global", "helper_foreign"]


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


def test_find_scripts_hides_scratch_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "scratch_probe_ports", "probe which local ports are listening")
    _write(conn, "list_open_ports", "list which local ports are listening")
    names = [row["name"] for row in find_scripts(conn, "local ports listening")]
    assert names == ["list_open_ports"]


def test_find_scripts_query_starting_with_scratch_opts_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "scratch_probe_ports", "probe which local ports are listening")
    names = [row["name"] for row in find_scripts(conn, "scratch probe ports")]
    assert names == ["scratch_probe_ports"]


# --- Phase 6: the 30-query labeled set (docs/delegation-plan.md §4, 6a) --------------------------
# Copies of the seeds' names and descriptions (verbs never imports seeds) plus synthetic
# distractors. `name: description` per entry; an indented line continues the one above.
FIXTURE_LIBRARY = """
run_cmd: run one program with its arguments, no shell: run_cmd git status — the escape
    hatch for CLIs (git, uv, curl, ssh, docker); no pipes or &&
run_py: run a one-off python snippet from stdin (args become sys.argv) — the quick path
    for chaining/filtering that used to be a shell pipeline
read_file: read a file: print its text or source code, whole or by line range
write_file: create or overwrite a file: writes stdin to a path
edit_file: edit a file in place: replace one exact match of old text with new text
apply_patch: patch, edit or fix files by applying a unified diff (git apply or patch -p1)
grep_tree: search the codebase for text or a regex: grep_tree PATTERN [PATH] — no flags,
    line numbers and .gitignore respect built in
list_dir: list files in a directory: type, size and name, for exploring the workspace
stats: usage report: total runs, shell-escape rate, reuse rate, active library %
gardener: dup/stale sweep proposing archive candidates, never archives itself
export_library: dump the latest version of every non-archived script to a directory tree
tmux_start: start a long-lived job in the background (dev server, watcher, build, REPL) as a
    tmux window that outlives the run: tmux_start [--kind agent] JOB PROGRAM [ARG...] — no shell
tmux_list: what is running? list the jobs started by tmux_start: name, kind, alive or dead
    with exit code, age
tmux_read: read or tail a job's output: its last lines from the live screen, or from its log with
    the exit code once it is dead: tmux_read JOB [LINES] [SKIP]
tmux_send: type one line into a live job and press Enter (a prompt, a REPL, a TUI):
    tmux_send JOB TEXT — typed as-is
tmux_stop: stop or kill a job started by tmux_start and free its name: tmux_stop JOB — closes its
    tmux window, keeps its log
tmux_wait: wait for a job (a build, a server) to end — is it done? blocks on its signal, no
    polling: tmux_wait grim-JOB [SECONDS] (exit 3 if it ended badly, 124 if it has not)
agent_start: delegate a task to another coding agent (claude, codex, pi) in a tmux window:
    agent_start AGENT JOB DIR with the brief on stdin; it answers in result.md
agent_read: read a delegate started by agent_start: its state (DONE, WORKING, NEEDS-INPUT,
    IDLE, EXITED) and its answer from result.md, or its screen: agent_read JOB
agent_done: a delegate runs this when its answer is written: agent_done JOB checks result.md
    (headings, size) and only then marks the job DONE and signals grim-JOB
agent_wait: wait for delegates (claude, codex) to finish — is it done? blocks once until every job
    named is over or SECONDS pass, no polling:
    agent_wait SECONDS JOB [JOB...] — 0 all DONE, 3 any failed, 124 still running
agent_exec: run a delegate headless on <run_dir>/JOB/brief.md with stated powers (agent_ask
    starts it): agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH — 0 only for DONE
agent_ask: ask codex, ask claude, or both one question headless (a review, a plan, a second
    opinion), each delegate in its own tmux job:
    agent_ask --exec-args JSON AGENTS ID DIR [SECONDS] (brief on stdin)
agent_compare: compare two delegates' answers with no model: agent_compare JOB_A JOB_B prints
    one packet (<= 4 KiB): verdicts AGREE/DIFFER, agreed and only-A/only-B claims, evidence
run_dir_prune: free the run dir of old job logs and job dirs whose tmux window is gone:
    run_dir_prune [--keep-days N] [--max-mb M] [--yes] — lists only, without --yes
web_open: open a URL in a new tab of grim's own long-lived Chrome (starts it if needed;
    logins and tabs last between scripts): web_open URL
web_read: read the visible text of an element in the current browser tab, waiting for it
    to show: web_read SELECTOR [SECONDS]
web_type: type text into a field in the current browser tab; a secret is given by env var
    name, never as text: web_type SELECTOR TEXT | --env NAME
old_claude_fleet: manage Claude Code sessions in tmux: launch, status, capture, send, stop.
    Search: claude session tmux background agent window send prompt status tail resume
docker_containers: list running docker containers with their image and status
deploy_site: build the static site and copy it to the web host
tmux_list_panes: tmux list: list tmux panes (tmux list-panes, tmux list-windows) per window
"""
FIXTURE_BODIES = {"deploy_site": 'print("tmux list jobs, read job, stop it, ask codex")'}
# name runs successes: the real library's heavy hitters, so a usage prior is ranked under pressure.
FIXTURE_RUNS = """run_py 1700 1600, read_file 1100 1020, grep_tree 250 180, list_dir 220 195,
run_cmd 170 145, old_claude_fleet 150 110, export_library 120 120, edit_file 55 43, stats 50 44,
write_file 45 32, deploy_site 40 40, tmux_start 6 5, tmux_read 3 3"""
# query | acceptable answers | today's top-1. Correct = any acceptable answer in the top 3.
LABELED_QUERIES = """
what is running | tmux_list | docker_containers
tmux list | tmux_list | tmux_list
read job | tmux_read | tmux_read
stop it | tmux_stop | old_claude_fleet
ask codex | agent_ask | agent_ask
docker processes | run_cmd docker_containers | run_cmd
read a file | read_file | read_file
write a file | write_file | write_file
edit a file in place | edit_file | edit_file
is it done | tmux_wait agent_read agent_wait | agent_done
tail the job log | tmux_read | tmux_read
kill it | tmux_stop | tmux_stop
wait for the build | tmux_wait | tmux_wait
ask claude | agent_ask | agent_ask
which delegate | agent_stats agent_ask | agent_read
what jobs are running | tmux_list | tmux_list
search the code for a regex | grep_tree | grep_tree
list files in a directory | list_dir | list_dir
apply a diff | apply_patch | apply_patch
start a dev server in the background | tmux_start | tmux_start
type into the repl | tmux_send | tmux_send
review this change with another agent | agent_ask agent_start | agent_start
compare the two answers | agent_compare | agent_compare
what did the delegate say | agent_read | agent_read
open a web page | web_open | web_open
run a python snippet | run_py | run_py
run git status | run_cmd | run_cmd
usage report | stats | stats
clean up old job logs | run_dir_prune | run_dir_prune
wait for codex to finish | agent_wait tmux_wait | agent_wait
"""
TODAY_TOP3_HITS = 29
GATE_TOP3_HITS = 27  # delegation plan 6b: >=27/30 correct in the top 3
LABELED_QUERY_COUNT = 30


def fixture_entries() -> list[tuple[str, str]]:
    found = re.findall(r"^(\w+): (.*(?:\n .*)*)", FIXTURE_LIBRARY, re.MULTILINE)
    entries = [(name, " ".join(text.split())) for name, text in found]
    assert len({name for name, _ in entries}) == len(entries), "fixture names are unique"
    return entries


def labeled_queries() -> list[tuple[str, tuple[str, ...], str]]:
    rows = [line.split(" | ") for line in LABELED_QUERIES.strip().splitlines()]
    parsed = [(query, tuple(ok.split()), today) for query, ok, today in rows]
    assert len(parsed) == LABELED_QUERY_COUNT, "the find gate is defined over exactly 30 queries"
    return parsed


def _fixture_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = _migrated_conn(tmp_path, monkeypatch)
    for name, description in fixture_entries():
        body = FIXTURE_BODIES.get(name, f'"""{name}."""\nprint(1)')
        write_script(conn, WriteRequest(name, "python", description, body, None, "global", "s"))
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES ('fixture-runs', 'agent')")
    for entry in FIXTURE_RUNS.replace("\n", " ").split(", "):
        name, runs, ok = entry.split()
        conn.executemany(
            "INSERT INTO execution (script_version_id, session_id, seq, exit_code) SELECT v.id, "
            "'fixture-runs', (SELECT COUNT(*) FROM execution), ? FROM script_version v "
            "JOIN script s ON s.id = v.script_id WHERE s.name = ?",
            [(int(i >= int(ok)), name) for i in range(int(runs))],
        )
    conn.commit()
    return conn


def top_names(conn: sqlite3.Connection, query: str, limit: int = 3) -> list[str]:
    return [row["name"] for row in find_scripts(conn, query, limit=limit)]


def test_labeled_queries_pin_todays_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _fixture_library(tmp_path, monkeypatch)
    rows = labeled_queries()
    got = {query: (top_names(conn, query, 1) or ["-"])[0] for query, _, _ in rows}
    assert got == {query: today for query, _, today in rows}
    hits = [query for query, ok, _ in rows if set(ok) & set(top_names(conn, query))]
    assert len(hits) == TODAY_TOP3_HITS >= GATE_TOP3_HITS, hits  # the phase 6 gate
    for name, _ in fixture_entries():  # gate: every slug, or its spaced form, is top-1
        for query in (name, name.replace("_", " "), f"  {name.upper()} "):
            assert top_names(conn, query, 1) == [name], query


def test_popular_scripts_cannot_buy_a_query_they_barely_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_file (1100 runs) shares only "read" with "read job"; run_py (1700) only stopwords
    with "wait for codex to finish". Neither may win on runs; a body-only hit never leads."""
    conn = _fixture_library(tmp_path, monkeypatch)
    assert top_names(conn, "read job", 1) == ["tmux_read"]
    assert top_names(conn, "wait for codex to finish", 1) == ["agent_wait"]
    assert top_names(conn, "edit a file in place", 1) == ["edit_file"]
    tmux_hits = top_names(conn, "tmux list jobs", 5)
    assert tmux_hits[0] == "tmux_list" and "deploy_site" not in tmux_hits, tmux_hits


def test_usage_breaks_a_near_tie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    for name in ("helper_fresh", "helper_proven"):
        _write(conn, name, "handles failing tests")
    conn.execute("INSERT INTO session (id, kind) VALUES ('s', 'agent')")
    conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code) "
        "VALUES ((SELECT MAX(id) FROM script_version), 's', 1, 0)"
    )
    hits = find_scripts(conn, "failing tests")
    assert [(h["name"], h["runs"]) for h in hits] == [("helper_proven", 1), ("helper_fresh", 0)]
