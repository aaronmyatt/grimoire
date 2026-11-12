# Feature 7 — Recipes compound

Status: **concept, nothing built** (written 2026-11-12).
Concept: [surfaces.md](../surfaces.md). Slice: `adapter` (prompt) and evals. Build order: 7 of 8.

- As the human, I want every driven task to leave a script I can reuse.
- This is the old thesis, kept: scripts by default.
- A recipe is a named python script that calls driver seeds.

```gherkin
Feature: Recipes compound

  @eval
  Scenario: Reuse before write
    Given recipe "deploy_and_check" is in the library
    When the task is "deploy and check the login page"
    Then the agent finds it, reads it and runs it
    And writes nothing new

  @eval
  Scenario: New work becomes a recipe
    Given no script fits the task
    When the agent drives tmux and Chrome to finish it
    Then one named python recipe is left that calls the seeds
    And it runs again with new args

  @eval
  Scenario: A probe stays a one-off
    When the agent only peeks at a pane or a page once
    Then it uses a seed call or a scratch_ script
    And find does not show it later

  @seeds
  Scenario: Recipes compose through grim run
    Given a recipe that uses tmux and Chrome
    Then it calls the seeds with `grim run`, never by import
    And it checks the exit code of each call

  @seeds
  Scenario: Everything is on the record
    When a recipe sends keys to a delegate and clicks a button
    Then each send and click is an execution row with argv, exit code and time

  @eval
  Scenario: The library grows
    Given a batch of driver tasks was run
    When I run grim stats
    Then the count of named recipes went up
    And the share of one-off snippet runs went down
```

## Checked so far

- Nothing in this feature is checked yet.
- Known risk, from the python-pivot A/B: the agent wrote 0 named scripts and used `run_py`
  snippets. "The library grows" is the scenario that watches for this.
