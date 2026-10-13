"""Integration tests for metrics/datasets.py — every dataset built from one
small library and span log. The roll-up arithmetic itself is unit-tested in
test_periods.py, the session join in test_sessions.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grim import db
from grim.metrics import datasets, periods, sessions

_OPEN_MS = 1_791_190_800_000  # 2026-10-05 09:00:00 UTC (ISO 2026-W41)
_GENERATED = "2026-10-15 00:00:00"
_TOTAL_RUNS = 3
_FAIL_PCT = 33.3

_SQL = """
INSERT INTO session (id, kind, started_at) VALUES
  ('s1', 'agent', '2026-10-05 09:00:30'), ('s2', 'agent', '2026-10-13 09:00:00');
INSERT INTO script (id, name, language, description, seeded, origin_session_id, created_at)
VALUES (1, 'fetch_page', 'python', 'd', 0, 's1', '2026-10-05 09:01:00');
INSERT INTO script_version (id, script_id, version, body, body_hash) VALUES (1, 1, 1, 'b', 'h');
INSERT INTO execution (script_version_id, session_id, seq, exit_code, started_at) VALUES
  (1, 's1', 1, 0, '2026-10-05 09:02:00'), (1, 's1', 2, 1, '2026-10-05 09:03:00'),
  (1, 's2', 1, 0, '2026-10-13 09:01:00');
"""


@pytest.fixture
def source(tmp_path: Path) -> datasets.Source:
    conn = db.connect(tmp_path / "grimoire.db")
    db.migrate(conn)
    conn.executescript(_SQL)
    log = tmp_path / "spans.jsonl"
    log.write_text(
        json.dumps({"kind": "session", "phase": "open", "session": "s1", "ts": _OPEN_MS})
    )
    return datasets.Source(conn, log, "", _GENERATED)


def test_every_dataset_builds_with_its_declared_columns(source: datasets.Source) -> None:
    expected = {
        "summary": periods.PERIOD_COLUMNS,
        "weekly": periods.PERIOD_COLUMNS,
        "sessions": sessions.SESSION_COLUMNS,
        "runs": datasets.RUN_COLUMNS,
        "scripts": datasets.SCRIPT_COLUMNS,
    }
    assert set(expected) == set(datasets.DATASETS)
    for name, columns in expected.items():
        dataset = datasets.build(name, source)
        assert (dataset.name, dataset.columns) == (name, columns)
        assert dataset.meta == {"generated_at": _GENERATED, "since": "all"}


def test_weekly_and_summary_roll_up_the_same_runs(source: datasets.Source) -> None:
    weekly = datasets.build("weekly", source).rows
    assert [row["period"] for row in weekly] == ["2026-W41", "2026-W42"]
    (summary,) = datasets.build("summary", source).rows
    assert (summary["period"], summary["runs"], summary["fail_pct"]) == (
        datasets.ALL_TIME_PERIOD,
        _TOTAL_RUNS,
        _FAIL_PCT,
    )
    assert summary["runs"] == sum(int(row["runs"] or 0) for row in weekly)


def test_since_narrows_the_build(source: datasets.Source) -> None:
    later = datasets.Source(source.conn, source.spans_path, "2026-10-10", _GENERATED)
    assert [row["period"] for row in datasets.build("weekly", later).rows] == ["2026-W42"]
    assert [row["session_id"] for row in datasets.build("sessions", later).rows] == ["s2"]
    assert datasets.build("runs", later).meta["since"] == "2026-10-10"


def test_builds_never_write_to_the_library(source: datasets.Source) -> None:
    before = source.conn.total_changes
    for name in datasets.DATASETS:
        datasets.build(name, source)
    assert source.conn.total_changes == before
    assert type(datasets.build("runs", source).rows[0]) is dict
