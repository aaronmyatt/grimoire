"""Tests for seeds/loader.py — idempotent seeding via write_script."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grim import db
from grim.seeds.bodies import SEEDS
from grim.seeds.loader import RETIRED_SEEDS, enabled_seeds, load_seeds
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
    drifted = update.UpdateRequest(name="list_dir", changelog="local drift", body='print("old")\n')
    update.update_script(conn, drifted)

    second_pass = load_seeds(conn)

    assert second_pass == ["list_dir@3"]  # v1 seeded, v2 drift, v3 re-sync
    bundled = next(seed.body for seed in SEEDS if seed.name == "list_dir")
    assert _latest(conn, "list_dir")["body"] == bundled
    assert load_seeds(conn) == []  # converged: a third pass is a no-op


def test_load_seeds_resyncs_a_drifted_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    conn.execute("UPDATE script SET description = 'stale words' WHERE name = 'list_dir'")
    conn.commit()

    second_pass = load_seeds(conn)

    assert second_pass == ["list_dir@1"]  # description refreshed, body untouched
    bundled = next(seed.description for seed in SEEDS if seed.name == "list_dir")
    assert _latest(conn, "list_dir")["description"] == bundled


def test_load_seeds_never_touches_unseeded_or_archived_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the human took over (seeded=0) or shelved (archived=1) is out
    of sync-scope — re-running load_seeds must leave it exactly as found."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    conn.execute("UPDATE script SET seeded = 0, description = 'mine now' WHERE name = 'list_dir'")
    conn.execute("UPDATE script SET archived = 1, description = 'shelved' WHERE name = 'stats'")
    conn.commit()

    second_pass = load_seeds(conn)

    assert second_pass == []
    assert _latest(conn, "list_dir")["description"] == "mine now"
    assert _latest(conn, "stats")["description"] == "shelved"


def test_enabled_seeds_defaults_to_the_full_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRIM_BASE_SEEDS", raising=False)

    assert enabled_seeds() == SEEDS


def test_load_seeds_honors_a_base_seeds_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The eval knob: GRIM_BASE_SEEDS picks which bundled seeds land, so an
    experiment arm can run the same task set with and without `run_cmd`."""
    monkeypatch.setenv("GRIM_BASE_SEEDS", "read_file, write_file")  # spaces tolerated
    conn = _migrated_conn(tmp_path, monkeypatch)

    newly_seeded = load_seeds(conn)

    assert newly_seeded == ["read_file", "write_file"]  # roster order
    rows = conn.execute("SELECT name FROM script ORDER BY name").fetchall()
    assert [row["name"] for row in rows] == ["read_file", "write_file"]
    assert load_seeds(conn) == []  # subsetting stays idempotent


def test_load_seeds_seeds_nothing_on_an_explicitly_empty_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_BASE_SEEDS", "")
    conn = _migrated_conn(tmp_path, monkeypatch)

    assert load_seeds(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM script").fetchone()["n"] == 0


def test_load_seeds_rejects_an_unknown_seed_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in the env var must abort init loudly before any write —
    silently seeding a different set would corrupt an experiment arm."""
    monkeypatch.setenv("GRIM_BASE_SEEDS", "run_cmd,read_fiel")
    conn = _migrated_conn(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="read_fiel"):
        load_seeds(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM script").fetchone()["n"] == 0


def test_load_seeds_exclusion_never_removes_a_present_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing the set later leaves already-present seeds untouched (and
    unsynced) — the knob gates seeding, it never archives."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)  # full roster first
    monkeypatch.setenv("GRIM_BASE_SEEDS", "run_cmd")
    conn.execute("UPDATE script SET description = 'stale words' WHERE name = 'stats'")
    conn.commit()

    second_pass = load_seeds(conn)

    assert second_pass == []  # run_cmd in sync; excluded stats not re-synced
    assert _latest(conn, "stats")["description"] == "stale words"
    assert conn.execute("SELECT COUNT(*) AS n FROM script").fetchone()["n"] == len(SEEDS)


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


def test_load_seeds_resyncs_drift_even_when_python_is_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: once update gated an omitted --lang, the re-sync of a
    drifted python seed raised under a solo-language arm (python not
    writable). Re-sync is seeding too — the toggles must not gate it."""
    conn = _migrated_conn(tmp_path, monkeypatch)
    load_seeds(conn)
    drifted = update.UpdateRequest(name="read_file", changelog="drift", body='print("old")\n')
    update.update_script(conn, drifted)
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "")
    monkeypatch.setenv("GRIM_LANGUAGES", "jq")

    assert load_seeds(conn) == ["read_file@3"]
    bundled = next(seed.body for seed in SEEDS if seed.name == "read_file")
    assert _latest(conn, "read_file")["body"] == bundled


def _insert_bundled_shell(conn: sqlite3.Connection, seeded: int) -> None:
    """A library from before the pivot: a `shell` row as the old build wrote it."""
    script_id = conn.execute(
        "INSERT INTO script (name, language, description, seeded) "
        "VALUES ('shell', 'python', 'run any shell command', ?)",
        (seeded,),
    ).lastrowid
    conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) "
        "VALUES (?, 1, 'print(1)', 'h')",
        (script_id,),
    )
    conn.commit()


def test_load_seeds_archives_a_retired_seed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_bundled_shell(conn, seeded=1)

    assert "shell (retired)" in load_seeds(conn)
    row = conn.execute("SELECT archived FROM script WHERE name = 'shell'").fetchone()
    assert row["archived"] == 1
    assert load_seeds(conn) == []  # converged: retiring is idempotent


def test_load_seeds_leaves_a_taken_over_retired_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _migrated_conn(tmp_path, monkeypatch)
    _insert_bundled_shell(conn, seeded=0)  # a human's own script named shell

    assert "shell (retired)" not in load_seeds(conn)
    row = conn.execute("SELECT archived FROM script WHERE name = 'shell'").fetchone()
    assert row["archived"] == 0


def test_retired_seeds_are_never_bundled() -> None:
    assert not set(RETIRED_SEEDS) & {seed.name for seed in SEEDS}
