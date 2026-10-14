"""Wall-clock time budget for one grim-agent run (adapter slice).

A caller that will hard-kill the agent — the Telegram bot SIGKILLs a run at
its 900s cap — sets $GRIM_TIME_BUDGET_S so the agent can see the clock.
Every observation then ends with a time line, and past WRAP_UP_SHARE of the
budget that line tells the model to stop and submit what it has, so a long
task ends with a usable hand-off instead of a killed process and no answer.
Unset means no budget: the REPL, evals, and other callers are unchanged.

Pure functions over (elapsed, budget) — the caller supplies the clock.
"""

from __future__ import annotations

BUDGET_ENV = "GRIM_TIME_BUDGET_S"
BUDGET_MIN_S = 60
BUDGET_MAX_S = 86_400
# Share of the budget after which every observation says "submit now".
# 0.8 of the bot's budget leaves ~170s: enough for one or two turns.
WRAP_UP_SHARE = 0.8


def budget_s(raw: str | None) -> int | None:
    """Parse $GRIM_TIME_BUDGET_S (external input: validate, never trust).
    Unset, blank, non-integer, or non-positive -> None (no budget); anything
    else is clamped to [BUDGET_MIN_S, BUDGET_MAX_S]."""
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    if value <= 0:
        return None
    result = max(BUDGET_MIN_S, min(value, BUDGET_MAX_S))
    assert BUDGET_MIN_S <= result <= BUDGET_MAX_S, "a parsed budget is always clamped"
    return result


def remaining_s(elapsed_s: float, budget: int) -> float:
    """Seconds left in the budget, never negative."""
    assert budget > 0, "a budget is positive"
    left = max(0.0, budget - elapsed_s)
    assert left >= 0.0, "time left is never negative"
    assert elapsed_s < 0 or left <= budget, "time left never exceeds the budget"
    return left


def wrap_up_due(elapsed_s: float, budget: int) -> bool:
    """Whether the run is past WRAP_UP_SHARE of its budget."""
    assert budget > 0, "a budget is positive"
    assert 0.0 < WRAP_UP_SHARE < 1.0, "the wrap-up point lies inside the budget"
    return elapsed_s >= budget * WRAP_UP_SHARE


def time_note(elapsed_s: float, budget: int) -> str:
    """The line appended to each observation: plain time left, or — past the
    wrap-up point — an order to submit a progress hand-off now."""
    left = int(remaining_s(elapsed_s, budget))
    if not wrap_up_due(elapsed_s, budget):
        note = f"[grim] time: {int(elapsed_s)}s used of a {budget}s budget, {left}s left."
    else:
        note = (
            f"[grim] time is nearly up: {left}s left of a {budget}s budget, then this run "
            "is killed with no answer. Stop exploring. Call submit now: say what you did, "
            "what is left, and how to continue."
        )
    assert note.startswith("[grim] time"), "the note is always grim-branded"
    return note
