"""Tests for metrics/periods.py — pure roll-ups over hand-made rows."""

from __future__ import annotations

import pytest

from grim.metrics import periods
from grim.metrics.formats import Cell

_ACTIVE_S1, _ACTIVE_S3 = 40_000, 1_000
_LLM_S1 = 3
_REUSE_W42_PCT = 66.7
_ONE_THIRD_PCT = 33.3
_HALF_PCT = 50.0


def _run(session: str, kind: str, when: str, **flags: int) -> dict[str, Cell]:
    return {
        "session_id": session,
        "session_kind": kind,
        "started_at": when,
        "exit_code": flags.get("exit_code", 0),
        "escape_hatch": flags.get("escape", 0),
        "seeded": flags.get("seeded", 0),
        "reuse": flags.get("reuse", 0),
    }


def _session(sid: str, when: str | None, active: int | None, llm: int | None) -> dict[str, Cell]:
    return {"session_id": sid, "kind": "agent", "started_at": when, "active_ms": active,
            "llm_calls": llm, "tokens": None, "format_errors": 1}  # fmt: skip


_INPUTS = periods.Inputs(
    runs=[
        _run("s1", "agent", "2026-10-05 09:02:00", seeded=1),
        _run("s1", "agent", "2026-10-05 09:03:00", exit_code=1),
        _run("s2", "agent", "2026-10-13 09:01:00", reuse=1),
        _run("s2", "agent", "2026-10-13 09:02:00", escape=1),
        _run("human-adhoc", "human", "2026-10-13 10:00:00", reuse=1),
    ],
    sessions=[
        _session("s1", "2026-10-05 09:00:00", _ACTIVE_S1, _LLM_S1),
        _session("s2", "2026-10-13 09:00:00", None, None),  # no span data
        _session("s3", "2026-10-14 08:00:00", _ACTIVE_S3, 1),
        {**_session("human-adhoc", "2026-09-01 00:00:00", None, None), "kind": "human"},
    ],
    scripts=[
        {"seeded": 0, "created_at": "2026-10-05 09:01:00"},
        {"seeded": 1, "created_at": "2026-10-05 09:01:00"},  # seeds are never "written"
        {"seeded": 0, "created_at": "2026-09-30 00:00:00"},  # before since: not new
    ],
    since="2026-10-01",
)


def test_weekly_rows_split_runs_sessions_and_scripts_by_week() -> None:
    w41, w42 = periods.period_rows(_INPUTS, periods.iso_week)
    assert (w41["period"], w41["runs"], w41["fail_pct"], w41["seed_pct"]) == (
        "2026-W41",
        2,
        50.0,
        50.0,
    )
    assert (w41["agent_sessions"], w41["library_hit_pct"], w41["scripts_written"]) == (1, 0.0, 1)
    assert (w41["active_ms_p50"], w41["llm_calls_p50"], w41["format_errors"]) == (
        _ACTIVE_S1,
        _LLM_S1,
        1,
    )
    assert (w42["runs"], w42["reuse_pct"], w42["escape_pct"]) == (3, _REUSE_W42_PCT, _ONE_THIRD_PCT)
    assert (w42["human_runs"], w42["agent_sessions"], w42["library_hit_pct"]) == (1, 2, 100.0)
    assert (w42["active_ms_p50"], w42["tokens_p50"]) == (_ACTIVE_S3, None)


def test_summary_is_one_row_over_everything() -> None:
    (row,) = periods.period_rows(_INPUTS, lambda _: "all")
    assert (row["period"], row["runs"], row["fail_pct"], row["agent_sessions"]) == (
        "all",
        5,
        20.0,
        3,
    )
    assert row["library_hit_pct"] == _HALF_PCT  # s2 hit, s1 did not


def test_rows_without_a_timestamp_land_in_unknown() -> None:
    inputs = periods.Inputs(
        runs=[], sessions=[_session("x", None, None, None)], scripts=[], since=""
    )
    (row,) = periods.period_rows(inputs, periods.iso_week)
    assert (row["period"], row["agent_sessions"], row["fail_pct"]) == (
        periods.UNKNOWN_PERIOD,
        1,
        None,
    )


@pytest.mark.parametrize(
    ("values", "fraction", "expected"),
    [([], 0.5, None), ([5], 0.9, 5), ([1, 2, 3, 4], 0.5, 2), ([1, 2, 3, 4], 0.9, 4)],
)
def test_percentile_is_nearest_rank(values: list[int], fraction: float, expected: int) -> None:
    assert periods.percentile(values, fraction) == expected


def test_pct_and_iso_week() -> None:
    assert periods.pct(1, 3) == _ONE_THIRD_PCT and periods.pct(0, 0) is None
    assert periods.iso_week("2026-01-01 00:00:00") == "2026-W01"
    assert periods.iso_week("2024-12-30 12:00:00") == "2025-W01"  # ISO year rolls early
