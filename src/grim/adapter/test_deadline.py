"""Unit tests for grim.adapter.deadline — the run's wall-clock budget.
Pure functions: the clock is passed in as elapsed seconds."""

from __future__ import annotations

from grim.adapter import deadline

_BUDGET = 900
_EARLY_S = 100.0
_LATE_S = 760.0  # past 0.8 * 900 = 720


def test_budget_unset_blank_or_garbage_means_no_budget() -> None:
    assert deadline.budget_s(None) is None
    assert deadline.budget_s("  ") is None
    assert deadline.budget_s("soon") is None
    assert deadline.budget_s("0") is None
    assert deadline.budget_s("-5") is None


def test_budget_parses_and_clamps() -> None:
    assert deadline.budget_s(" 870 ") == 870  # noqa: PLR2004
    assert deadline.budget_s("5") == deadline.BUDGET_MIN_S
    assert deadline.budget_s("999999999") == deadline.BUDGET_MAX_S


def test_remaining_never_goes_negative() -> None:
    assert deadline.remaining_s(_EARLY_S, _BUDGET) == _BUDGET - _EARLY_S
    assert deadline.remaining_s(5_000.0, _BUDGET) == 0.0


def test_early_note_reports_time_left_without_asking_to_stop() -> None:
    note = deadline.time_note(_EARLY_S, _BUDGET)
    assert "800s left" in note
    assert "submit" not in note


def test_late_note_orders_a_progress_handoff() -> None:
    assert not deadline.wrap_up_due(_EARLY_S, _BUDGET)
    assert deadline.wrap_up_due(_LATE_S, _BUDGET)
    note = deadline.time_note(_LATE_S, _BUDGET)
    assert "140s left" in note
    assert "Call submit now" in note


def test_a_run_that_fits_the_time_left_is_unchanged() -> None:
    assert deadline.clamp_run_timeout(None, 500.0) is None
    assert deadline.clamp_run_timeout(30.0, 500.0) == 30.0  # noqa: PLR2004


def test_a_run_longer_than_the_time_left_is_cut_to_it() -> None:
    assert deadline.clamp_run_timeout(900.0, 300.0) == 300.0  # noqa: PLR2004
    assert deadline.clamp_run_timeout(None, 50.0) == 50.0  # noqa: PLR2004 - default 120 > 50
    assert deadline.clamp_run_timeout(0.0, 50.0) == 50.0  # noqa: PLR2004 - 0 means default


def test_a_clamped_run_keeps_a_minimum_timeout() -> None:
    assert deadline.clamp_run_timeout(60.0, 0.0) == deadline.RUN_MIN_TIMEOUT_S
