"""Tests for curate/near.py."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.curate import _shared
from grim.curate.near import cmd_near, neighbors
from grim.verbs.write import WriteRequest, write_script


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def _write(conn: sqlite3.Connection, name: str) -> None:
    write_script(
        conn,
        WriteRequest(
            name=name,
            language="python",
            description="d",
            body="print(1)",
            parent=None,
            scope="global",
            session_id="human-adhoc",
        ),
    )


def _run_sequence(conn: sqlite3.Connection, session: str, names: list[str]) -> None:
    """Record `names` as consecutive executions in one session, so their seq
    adjacency populates the script_affinity view (name[i] runs before name[i+1])."""
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES (?, 'human')", (session,))
    for seq, name in enumerate(names):
        version = conn.execute(
            "SELECT sv.id FROM script_version sv JOIN script s ON s.id = sv.script_id "
            "WHERE s.name = ? ORDER BY sv.version DESC LIMIT 1",
            (name,),
        ).fetchone()
        conn.execute(
            "INSERT INTO execution (script_version_id, session_id, seq, exit_code) "
            "VALUES (?, ?, ?, 0)",
            (version["id"], session, seq),
        )
    conn.commit()


def test_neighbors_reports_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    for name in ("build_step", "deploy_step", "notify_step"):
        _write(conn, name)
    # build -> deploy -> notify, run in this many sessions, so adjacency counts
    # are non-trivial (each session contributes one build->deploy adjacency).
    sessions = 2
    for i in range(sessions):
        _run_sequence(conn, f"s{i}", ["build_step", "deploy_step", "notify_step"])

    precedes, follows = neighbors(conn, "deploy_step")
    assert [r["name"] for r in precedes] == ["build_step"]
    assert [r["name"] for r in follows] == ["notify_step"]
    assert precedes[0]["times_adjacent"] == sessions


def test_neighbors_excludes_self_adjacency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _write(conn, "retry_step")
    # A script rerun back-to-back must not show up as its own neighbour.
    _run_sequence(conn, "s1", ["retry_step", "retry_step"])
    precedes, follows = neighbors(conn, "retry_step")
    assert precedes == []
    assert follows == []


def test_neighbors_unknown_name_raises_lookup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    with pytest.raises(LookupError):
        neighbors(conn, "does_not_exist")


def test_cmd_near_unknown_name_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _migrated_conn(tmp_path, monkeypatch).close()  # GRIM_DB set; cmd_near opens its own conn
    exit_code = cmd_near(argparse.Namespace(name="ghost", limit=None))
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err
