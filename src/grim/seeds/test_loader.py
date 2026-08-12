"""Tests for seeds/loader.py — idempotent seeding via write_script."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.seeds.bodies import SEEDS
from grim.seeds.loader import load_seeds
from grim.verbs import _shared


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
