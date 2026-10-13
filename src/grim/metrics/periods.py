"""Roll-ups for `grim stats weekly|summary` (metrics slice): pure functions
over already-fetched run, session and script rows — the scorecard from
docs/metrics-research.md, one row per period.

Columns: *_pct are shares of runs; library_hit_pct is the share of agent
sessions with runs that ran at least one reused script; active_ms is wall
time minus time waiting on the human; p50/p90 are nearest-rank percentiles
over agent sessions that have span data. Runs land in the period they ran;
sessions in the period they started; scripts in the period they were made.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Callable, Sequence

from grim.metrics.formats import Cell

PERIOD_COLUMNS = (
    "period", "runs", "failed_runs", "fail_pct", "escape_pct", "seed_pct", "reuse_pct",
    "human_runs", "agent_sessions", "library_hit_pct", "scripts_written", "active_ms_p50",
    "active_ms_p90", "llm_calls_p50", "tokens_p50", "format_errors",
)  # fmt: skip
HUMAN_KIND = "human"
UNKNOWN_PERIOD = "unknown"  # a row with no timestamp still counts somewhere
Rows = Sequence[dict[str, Cell]]


@dataclasses.dataclass(frozen=True)
class Inputs:
    """The raw rows a roll-up reads; `since` bounds which scripts count as
    new (runs and sessions arrive already filtered)."""

    runs: Rows
    sessions: Rows
    scripts: Rows
    since: str


@dataclasses.dataclass
class _Bucket:
    runs: int = 0
    failed: int = 0
    escape: int = 0
    seed: int = 0
    reuse: int = 0
    human_runs: int = 0
    run_sessions: set[str] = dataclasses.field(default_factory=set)
    hit_sessions: set[str] = dataclasses.field(default_factory=set)
    agent_sessions: int = 0
    scripts_written: int = 0
    active_ms: list[int] = dataclasses.field(default_factory=list)
    llm_calls: list[int] = dataclasses.field(default_factory=list)
    tokens: list[int] = dataclasses.field(default_factory=list)
    format_errors: int = 0


def period_rows(inputs: Inputs, key: Callable[[str], str]) -> list[dict[str, Cell]]:
    """One row per `key(timestamp)` period (iso_week, or a constant for the
    all-time summary), sorted by period label."""
    buckets: dict[str, _Bucket] = {}

    def bucket(ts: Cell) -> _Bucket:
        return buckets.setdefault(key(str(ts)) if ts else UNKNOWN_PERIOD, _Bucket())

    _add_runs(inputs.runs, bucket)
    _add_sessions(inputs.sessions, bucket)
    for script in inputs.scripts:
        if not script["seeded"] and str(script["created_at"] or "") >= inputs.since:
            bucket(script["created_at"]).scripts_written += 1
    rows = [_row(period, b) for period, b in sorted(buckets.items())]
    assert all(tuple(row) == PERIOD_COLUMNS for row in rows), "rows follow PERIOD_COLUMNS"
    return rows


def _add_runs(rows: Rows, bucket: Callable[[Cell], _Bucket]) -> None:
    for run in rows:
        b, sid = bucket(run["started_at"]), str(run["session_id"])
        b.runs += 1
        b.failed += run["exit_code"] != 0
        b.escape += bool(run["escape_hatch"])
        b.seed += bool(run["seeded"])
        b.reuse += bool(run["reuse"])
        if run["session_kind"] == HUMAN_KIND:
            b.human_runs += 1
            continue
        b.run_sessions.add(sid)
        if run["reuse"]:
            b.hit_sessions.add(sid)


def _add_sessions(rows: Rows, bucket: Callable[[Cell], _Bucket]) -> None:
    for session in rows:
        if session["kind"] == HUMAN_KIND:
            continue
        b = bucket(session["started_at"])
        b.agent_sessions += 1
        b.format_errors += int(session["format_errors"] or 0)
        for name in ("active_ms", "llm_calls", "tokens"):
            value = session[name]
            if isinstance(value, int | float):
                getattr(b, name).append(int(value))


def _row(period: str, b: _Bucket) -> dict[str, Cell]:
    return {
        "period": period,
        "runs": b.runs,
        "failed_runs": b.failed,
        "fail_pct": pct(b.failed, b.runs),
        "escape_pct": pct(b.escape, b.runs),
        "seed_pct": pct(b.seed, b.runs),
        "reuse_pct": pct(b.reuse, b.runs),
        "human_runs": b.human_runs,
        "agent_sessions": b.agent_sessions,
        "library_hit_pct": pct(len(b.hit_sessions), len(b.run_sessions)),
        "scripts_written": b.scripts_written,
        "active_ms_p50": percentile(b.active_ms, 0.5),
        "active_ms_p90": percentile(b.active_ms, 0.9),
        "llm_calls_p50": percentile(b.llm_calls, 0.5),
        "tokens_p50": percentile(b.tokens, 0.5),
        "format_errors": b.format_errors,
    }


def pct(part: int, whole: int) -> float | None:
    """part/whole as a percentage to one decimal; None when whole is 0."""
    assert part >= 0 and whole >= 0, "counts are never negative"
    assert part <= whole or whole == 0, "a part never exceeds its whole"
    return round(100 * part / whole, 1) if whole else None


def percentile(values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile; None for no values.
    Ref: https://en.wikipedia.org/wiki/Percentile#The_nearest-rank_method"""
    assert 0 < fraction <= 1, "a percentile fraction is in (0, 1]"
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def iso_week(ts: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' -> ISO week label 'YYYY-Www' (sorts as text).
    Ref: https://docs.python.org/3/library/datetime.html#datetime.date.isocalendar"""
    iso = dt.date.fromisoformat(ts[:10]).isocalendar()
    label = f"{iso.year}-W{iso.week:02d}"
    assert len(label) == len("2026-W01"), "week labels sort as text"
    return label
