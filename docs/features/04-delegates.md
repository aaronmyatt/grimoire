# Feature 4 — Delegate to another agent

Status: **built 2026-11-12** as `agent_start` and `agent_read`. Follow-up and stop are
`tmux_send` and `tmux_stop`: there is no `agent_send` or `agent_stop`. Tested against a
fake agent TUI. The real claude, codex and pi were only looked at on their start screens;
no model was called.
Concept: [surfaces.md](../surfaces.md). Slice: `seeds`. Build order: 4 of 8.

- As the agent, I want to hand a hard task to another agent and get the answer back.
- Folds in the library's own `claude_tmux` script.

```gherkin
# Ref: https://man.openbsd.org/tmux#send-keys
@seeds
Feature: Delegate to another agent

  Scenario Outline: Same seeds, any agent
    When the agent runs agent_start "<agent>" with job "fix-auth", a directory and a brief
    Then a "<agent>" TUI runs in window "fix-auth", in that directory
    And the window is tagged @grim_kind=agent
    And the typed brief ends with the done contract:
      """
      When done, write your answer to ~/.grimoire/run/fix-auth/result.md
      then run: tmux wait-for -S grim-fix-auth
      """

    Examples:
      | agent  |
      | claude |
      | codex  |
      | pi     |

  # Ref: https://man.openbsd.org/tmux#paste-buffer
  Scenario: A long brief arrives whole
    Given a brief of 40 lines
    When agent_start sends it
    Then the delegate gets one message, not 40     # bracketed paste

  Scenario: First-run dialogs are known
    Given claude asks "trust this folder?" on launch
    When agent_start sees the dialog
    Then it answers as the claude profile says
    And only then sends the brief

  Scenario: Collect the answer
    Given delegate "fix-auth" signalled done
    When the agent runs agent_read "fix-auth"
    Then it gets the text of result.md, not a screen scrape

  Scenario: The delegate forgot the contract
    Given the delegate went idle and wrote no result.md
    When tmux_wait times out and the agent runs agent_read "fix-auth"
    Then it gets the pane text marked "no result.md"

  Scenario: The delegate needs input
    Given the delegate shows a question it cannot pass
    When the agent runs agent_read "fix-auth"
    Then the state is NEEDS-INPUT with the question text

  Scenario: Follow up
    Given delegate "fix-auth" is idle
    When the agent runs agent_send "fix-auth" "now run the full suite"
    Then the text is typed and submitted in that pane

  Scenario: Stop a delegate
    Given delegate "fix-auth" is live
    When the agent runs agent_stop "fix-auth"
    Then the window is gone
    And result.md is kept

  Scenario: A delegate's powers are stated
    When agent_start launches any delegate
    Then its permission mode is passed on the command line
    And the mode comes from config, or the delegate's own default if unset
    And neither the brief nor the driving agent can raise it
```

## Checked so far

- tmux 3.8, on an isolated server, 2026-11-12: `send-keys` text shows in `capture-pane`.
- From the library: `claude_tmux` has driven claude this way for 153 runs.
- Not checked: codex and pi under `send-keys`, and their launch dialogs.
