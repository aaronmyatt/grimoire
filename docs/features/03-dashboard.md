# Feature 3 — tmux is the dashboard

Status: **concept, nothing built** (written 2026-11-12). Seed names are examples, not final.
Concept: [surfaces.md](../surfaces.md). Slice: `adapter`. Build order: 3 of 8.

- As the human, I want one place to watch everything grim drives, and to step in.

```gherkin
# Ref: https://man.openbsd.org/tmux#attach-session
@adapter
Feature: tmux is the dashboard

  Scenario: The agent starts at home
    Given I am in a terminal outside tmux
    When I run grim-agent "fix the tests"
    Then the agent runs in session "grim", window "agent"
    And my terminal is attached to it

  Scenario: Already inside tmux
    Given I am inside a tmux session of my own
    When I run grim-agent "fix the tests"
    Then the agent runs in my current pane
    And its jobs still open in session "grim"

  Scenario: A headless run has no window
    Given there is no terminal, as under a bot or launchd
    When grim-agent runs with -p
    Then no agent window is made
    And its jobs still open in session "grim"

  Scenario: See it all
    Given the agent started 2 jobs and 1 delegate
    When I run `tmux attach -t grim`
    Then I see one window for each, named by job

  Scenario: Take over a pane
    Given a delegate is stuck on a question
    When I type the answer into its pane
    Then the delegate carries on
    And the agent's next agent_read shows my answer

  Scenario: The prompt teaches surfaces
    Given the tmux seeds are in the library
    When the system prompt renders
    Then it lists them from the live seed roster
    And it tells the agent to wait with tmux_wait, never sleep-then-check
    And it no longer names run_bg, list_bg or stop_bg
```

## Checked so far

- Nothing in this feature is checked yet.
- The prompt change alters behaviour, so goldens of today's prompt land first (root
  `CLAUDE.md` §4).
