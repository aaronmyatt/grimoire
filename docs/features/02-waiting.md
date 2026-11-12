# Feature 2 — Wait on a signal

Status: **built 2026-11-12.** `tmux_wait`, plus a job-end hook in `tmux_start`. The
`@adapter` scenario is the run-timeout clamp that already existed (`test_environment.py`).
Concept: [surfaces.md](../surfaces.md). Slice: `seeds`. Build order: 2 of 8.

- As the agent, I want to wait without sleeping, so I spend no turns on polling.

```gherkin
# Ref: https://man.openbsd.org/tmux#wait-for
@seeds
Feature: Wait on a signal

  Scenario: The signal arrives
    Given the agent is blocked in tmux_wait "grim-build"
    When something runs `tmux wait-for -S grim-build`
    Then tmux_wait exits 0

  Scenario: The signal came first
    Given "grim-build" was signalled before anyone waited
    When the agent runs tmux_wait "grim-build"
    Then it exits 0 at once

  # Ref: https://man.openbsd.org/tmux#set-hook
  Scenario: A job signals when it ends
    Given job "build" was started by tmux_start
    When its command exits
    Then signal "grim-build" is sent with no help from the command
    And tmux_wait "grim-build" exits 0

  Scenario: The signal never comes
    When the agent runs tmux_wait "grim-build" with timeout 60
    And 60 seconds pass with no signal
    Then it exits 124 saying job "build" is still running

  @adapter
  Scenario: A wait cannot outlive the time budget
    Given GRIM_TIME_BUDGET_S leaves 40 seconds
    When the agent asks tmux_wait for 600 seconds
    Then the wait is cut to 40 seconds     # deadline.clamp_run_timeout, exists today
```

## Checked so far

- tmux 3.8, on an isolated server, 2026-11-12:
  - `wait-for` wakes on a signal.
  - A signal sent before the wait is remembered: a late `wait-for` returns at once.
  - A `pane-died` hook on the window sends the signal when the command exits.
