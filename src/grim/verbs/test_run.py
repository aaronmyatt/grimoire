"""Tests for verbs/run.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.verbs import _shared
from grim.verbs.run import RunRequest, run_script
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


def test_run_script_truncates_shell_more_aggressively_than_named_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    body = "for i in range(1, 60): print(f'line{i}')"
    _seed_script(conn, body=body)  # named script foo_bar
    write_script(
        conn,
        WriteRequest(
            name="shell",
            language="python",
            description="d",
            body=body,
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )

    named_result = run_script(conn, _request(name="foo_bar"))
    shell_result = run_script(conn, _request(name="shell"))

    assert "first 40 + last 10 of 59 lines" in named_result.observation
    assert "first 10 + last 3 of 59 lines" in shell_result.observation
