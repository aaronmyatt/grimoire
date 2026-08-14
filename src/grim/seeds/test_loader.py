"""Tests for seeds/loader.py — idempotent seeding via write_script."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.seeds.bodies import SEEDS
from grim.seeds.loader import load_seeds
from grim.verbs import _shared, update


def _migrated_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = _shared.connect()
    db.migrate(conn)
    return conn


def test_load_seeds_writes_every_seed_flagged_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)

    newly_seeded = load_seeds(conn)

    assert set(newly_seeded) == {seed.name for seed in SEEDS}
    rows = conn.execute("SELECT name, seeded, scope FROM script").fetchall()
    assert len(rows) == len(SEEDS)
    for row in rows:
        assert row["seeded"] == 1
        assert row["scope"] == "global"


def test_load_seeds_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)

    second_pass = load_seeds(conn)

    assert second_pass == []
    row = conn.execute("SELECT COUNT(*) AS n FROM script").fetchone()
    assert row["n"] == len(SEEDS)


def _latest(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT sv.body, sv.version, s.description, s.seeded FROM script s "
        "JOIN script_version sv ON sv.script_id = s.id "
        "WHERE s.name = ? ORDER BY sv.version DESC LIMIT 1",
        (name,),
    ).fetchone()
    assert row is not None, f"script {name!r} must exist"
    return row


def test_load_seeds_resyncs_a_drifted_seed_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library whose seed body diverged from the bundled copy (an older
    build seeded it, or an agent updated it) converges back on the bundled
    body at the next load — as a new append-only version, never a rewrite."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    drifted = update.UpdateRequest(name="shell", changelog="local drift", body='print("old")\n')
    update.update_script(conn, drifted)

    second_pass = load_seeds(conn)

    assert second_pass == ["shell@3"]  # v1 seeded, v2 drift, v3 re-sync
    bundled = next(seed.body for seed in SEEDS if seed.name == "shell")
    assert _latest(conn, "shell")["body"] == bundled
    assert load_seeds(conn) == []  # converged: a third pass is a no-op


def test_load_seeds_resyncs_a_drifted_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    conn.execute("UPDATE script SET description = 'stale words' WHERE name = 'shell'")
    conn.commit()

    second_pass = load_seeds(conn)

    assert second_pass == ["shell@1"]  # description refreshed, body untouched
    bundled = next(seed.description for seed in SEEDS if seed.name == "shell")
    assert _latest(conn, "shell")["description"] == bundled


def test_load_seeds_never_touches_unseeded_or_archived_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the human took over (seeded=0) or shelved (archived=1) is out
    of sync-scope — re-running load_seeds must leave it exactly as found."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    conn.execute("UPDATE script SET seeded = 0, description = 'mine now' WHERE name = 'shell'")
    conn.execute("UPDATE script SET archived = 1, description = 'shelved' WHERE name = 'stats'")
    conn.commit()

    second_pass = load_seeds(conn)

    assert second_pass == []
    assert _latest(conn, "shell")["description"] == "mine now"
    assert _latest(conn, "stats")["description"] == "shelved"


def test_load_seeds_ignores_language_toggles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: with python outside the writable set (a solo-jq eval
    arm), the old loader swallowed the write gate's rejection as "already
    seeded" and left the library completely empty. Seeding must land every
    seed regardless of the env toggles — they gate agent writing only."""
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "")  # no builtins writable
    monkeypatch.setenv("GRIM_LANGUAGES", "jq")  # non-empty set, python excluded
    conn = _migrated_conn(tmp_path, monkeypatch)

    newly_seeded = load_seeds(conn)

    assert set(newly_seeded) == {seed.name for seed in SEEDS}
    row = conn.execute("SELECT COUNT(*) AS n FROM script WHERE seeded = 1").fetchone()
    assert row["n"] == len(SEEDS)
