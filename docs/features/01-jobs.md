# Feature 1 — Jobs are tmux windows

Status: **concept, nothing built** (written 2026-11-12). Seed names are examples, not final.
Concept: [surfaces.md](../surfaces.md). Slice: `seeds`. Build order: 1 of 8.

- As the agent, I want work that outlives one run, so servers and builds don't die at the
  timeout.
- Replaces `run_bg`, `list_bg` and `stop_bg`.

```gherkin
# Ref: https://man.openbsd.org/tmux#new-window
@seeds
Feature: Jobs are tmux windows

  Scenario: Start a job
    Given no tmux server is running
    When the agent runs tmux_start with job "api" and command "uv run serve"
    Then session "grim" exists with window "api"
    And the window is tagged @grim_kind=job and @grim_job=api
    And the run returns while the command is still going

  Scenario: A job name is taken
    Given job "api" is live
    When the agent runs tmux_start with job "api" again
    Then it exits nonzero naming the live job
    And no second window is made

  Scenario: No shell in the command
    When the agent runs tmux_start with a command that holds "|", "&&" or "sh -c"
    Then it exits 2 saying shell lines are refused
    And nothing is started

  Scenario: List jobs with no registry file
    Given jobs "api" and "crawl" are live
    When the agent runs tmux_list
    Then it prints both with kind, age and alive or dead
    And no pid file or JSON file was read

  Scenario: Read a job's screen
    Given job "api" has printed 5000 lines
    When the agent runs tmux_read "api"
    Then it gets the last lines, up to a stated cap
    And it says how to page back for more

  Scenario: Type into a job
    Given job "repl" is a live python prompt
    When the agent runs tmux_send "repl" "print(1 + 1)"
    Then the text is typed as-is and Enter is pressed
    And the next tmux_read shows "2"

  # Ref: https://man.openbsd.org/tmux#pipe-pane
  Scenario: A dead job keeps its story
    Given job "api" printed "last words" and crashed with exit 3
    When the agent runs tmux_read "api"
    Then it gets "last words" from the job's log file
    And it gets "exit 3"

  Scenario: Stop a job
    Given job "api" is live
    When the agent runs tmux_stop "api"
    Then the window is gone
    And tmux_list no longer shows it

  Scenario: The old background seeds retire
    Given a library that still has run_bg, list_bg and stop_bg
    When I run grim init on the new build
    Then the three are archived and reported as retired
    And a copy I took over is left alone
    And nothing is deleted
```

## Checked so far

- tmux 3.8, on an isolated server, 2026-11-12:
  - A command given as several arguments runs with no shell.
  - Window tags work as the job list, and a filter shows only tagged windows.
  - A dead pane reports its exit code (`pane_dead_status`).
  - A dead pane's screen did **not** keep the job's output. A `pipe-pane` log did.
    So the log file, not the screen, is the record of a dead job.
