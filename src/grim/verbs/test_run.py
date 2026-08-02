"""Tests for verbs/run.py."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.run import (
    DEFAULT_TIMEOUT_S,
    MAX_CALL_DEPTH,
    MAX_TIMEOUT_S,
    CallDepthExceeded,
    RunRequest,
    resolve_timeout,
    run_script,
)
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _seed_script(conn: sqlite3.Connection, body: str = "print('hi')") -> None:
    write_script(
        conn,
        WriteRequest(
            name="foo_bar",
            language="python",
            description="d",
            body=body,
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def _request(**overrides: object) -> RunRequest:
    fields: dict[str, object] = {
        "name": "foo_bar",
        "version": None,
        "argv": [],
        "stdin": None,
        "cwd": None,
        "timeout": 30.0,
        "session_id": "human-adhoc",
    }
    fields.update(overrides)
    return RunRequest(**fields)  # type: ignore[arg-type]


def test_run_script_propagates_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    expected_exit_code = 3
    _seed_script(conn, body=f"import sys; sys.exit({expected_exit_code})")
    result = run_script(conn, _request())
    assert result.exit_code == expected_exit_code


def test_run_script_passes_argv_and_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn, body="import sys; print(sys.argv[1], sys.stdin.read().strip())")
    result = run_script(conn, _request(argv=["hello"], stdin="world"))
    assert result.exit_code == 0
    assert "hello world" in result.observation


def test_run_script_records_execution_with_incrementing_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    first = run_script(conn, _request())
    second = run_script(conn, _request())
    seqs = [
        row["seq"]
        for row in conn.execute(
            "SELECT seq FROM execution WHERE id IN (?, ?) ORDER BY seq",
            (first.execution_id, second.execution_id),
        )
    ]
    assert seqs == [1, 2]


def test_run_script_observation_has_header_and_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    result = run_script(conn, _request())
    assert f"exec #{result.execution_id}" in result.observation
    assert "foo_bar@1" in result.observation
    assert "--- stdout" in result.observation


def test_run_script_raises_on_unknown_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        run_script(conn, _request(name="does_not_exist"))


def test_run_script_shows_full_output_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn, body="for i in range(1, 60): print(f'line{i}')")

    result = run_script(conn, _request())  # no head/tail → full

    assert "--- stdout: 59 lines ---" in result.observation
    assert "line1\n" in result.observation
    assert "line59" in result.observation
    assert "skipped" not in result.observation


def test_run_script_limits_output_when_head_tail_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn, body="for i in range(1, 60): print(f'line{i}')")

    result = run_script(conn, _request(head_lines=40, tail_lines=10))

    assert "first 40 + last 10 of 59 lines" in result.observation
    assert "line41" not in result.observation
    assert "... (9 skipped) ..." in result.observation


def test_run_script_rejects_when_call_depth_at_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate being MAX_CALL_DEPTH `grim run`s deep: the guard must reject
    # before dispatch rather than let an unbounded/cyclic chain recurse.
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    monkeypatch.setenv("GRIM_CALL_DEPTH", str(MAX_CALL_DEPTH))

    with pytest.raises(CallDepthExceeded):
        run_script(conn, _request())


def test_run_script_allows_call_just_below_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn)
    monkeypatch.setenv("GRIM_CALL_DEPTH", str(MAX_CALL_DEPTH - 1))

    result = run_script(conn, _request())  # last permitted level still runs

    assert result.exit_code == 0


def test_run_script_exposes_incremented_depth_to_dispatched_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A top-level run (no GRIM_CALL_DEPTH set) must hand the dispatched
    # script depth 1, and restore the env to unset afterwards so the
    # in-process adapter path doesn't accumulate depth across turns.
    conn = _migrated_conn(tmp_path, monkeypatch)
    monkeypatch.delenv("GRIM_CALL_DEPTH", raising=False)
    _seed_script(conn, body="import os; print(os.environ['GRIM_CALL_DEPTH'])")

    result = run_script(conn, _request())

    assert "--- stdout: 1 lines ---\n1" in result.observation
    assert "GRIM_CALL_DEPTH" not in os.environ


def test_run_script_supports_nested_grim_run_without_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: ensure_session's uncommitted write used to stay open
    # across the whole (blocking) dispatch call, so a script that shells
    # out to `grim run` on another script would deadlock/fail with
    # "database is locked". Both calls share session "human-adhoc" (the
    # default for both the outer request and the nested subprocess, since
    # neither sets GRIM_SESSION), matching how composition would actually
    # be used within one agent session.
    conn = _migrated_conn(tmp_path, monkeypatch)
    _seed_script(conn, body="print('base result')")
    write_script(
        conn,
        WriteRequest(
            name="wrapper_thing",
            language="python",
            description="wraps foo_bar",
            body=(
                "import subprocess\n"
                "result = subprocess.run(['grim', 'run', 'foo_bar'], "
                "capture_output=True, text=True)\n"
                "print('wrapper saw:', 'base result' in result.stdout)\n"
            ),
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )

    result = run_script(conn, _request(name="wrapper_thing", timeout=30.0))

    assert result.exit_code == 0
    assert "wrapper saw: True" in result.observation

    seqs = [row["seq"] for row in conn.execute("SELECT seq FROM execution ORDER BY seq")]
    assert seqs == [1, 2], "the nested call and the outer call must both land, uncollided"


def test_resolve_timeout_prefers_explicit_over_env_and_default() -> None:
    # explicit --timeout wins even when $GRIM_TIMEOUT is set.
    explicit = 30.0
    assert resolve_timeout(explicit, "300") == explicit


def test_resolve_timeout_falls_back_to_env_then_default() -> None:
    env_seconds = 300.0
    assert resolve_timeout(None, "300") == env_seconds
    assert resolve_timeout(None, None) == DEFAULT_TIMEOUT_S


def test_resolve_timeout_ignores_malformed_or_nonpositive_env() -> None:
    assert resolve_timeout(None, "not-a-number") == DEFAULT_TIMEOUT_S
    assert resolve_timeout(None, "0") == DEFAULT_TIMEOUT_S
    assert resolve_timeout(None, "-5") == DEFAULT_TIMEOUT_S


def test_resolve_timeout_clamps_to_the_ceiling() -> None:
    # both an explicit runaway and a runaway env value are capped.
    assert resolve_timeout(999_999.0, None) == MAX_TIMEOUT_S
    assert resolve_timeout(None, "999999") == MAX_TIMEOUT_S
