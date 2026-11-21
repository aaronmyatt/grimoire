# Feature 4 — Delegate to another agent

Status: **built.** Two paths, one set of seeds (`src/grim/seeds/bodies_agent.py`,
`bodies_headless.py`, `bodies_delegate.py`, `bodies_compare.py`). Tested against fake
delegates on a private tmux server. No real model is called by the suite.
Concept: [surfaces.md](../surfaces.md). Slice: `seeds`. Build order: 4 of 8.

- As the agent, I want to hand a hard task to another agent and get the answer back.
- Folds in the library's own `claude_tmux` script.

## The surface

Every call is `run(name=SEED, args=[...])` (CLI: `grim run SEED -- ARGS`). A delegate is a tmux
job tagged `@grim_kind=agent`, in session `grim`. Its files live in `<run_dir>/JOB/`
(`$GRIM_RUN_DIR`, default `~/.grimoire/run`). Only `agent_done` declares DONE: it checks
`result.md` and writes `status.json`.

**Headless (the default for asks):** `agent_ask` → `agent_wait` → `agent_compare`.

| Seed | argv | Exit codes |
|---|---|---|
| `agent_ask` | `--exec-args JSON claude\|codex\|both ID DIR [SECONDS]`, brief on stdin | 0 started · 1 a job name is taken · 2 bad call or powers not stated · 127 a program is missing |
| `agent_wait` | `SECONDS JOB [JOB...]` (SECONDS ≤ 3500, ≤ 4 jobs) | 0 all DONE · 3 all over, any failed · 124 any still running · 2 bad call |
| `agent_compare` | `JOB_A JOB_B` | 0 both DONE · 3 only one (single source) · 1 neither · 2 bad call |
| `agent_stats` | `[DAYS]` (default 30) | 0 the table · 2 bad DAYS |

- `agent_ask` opens one job per agent, named `ID-claude` / `ID-codex`. Each runs `agent_exec`.
  - JSON states each delegate's powers, e.g. `'{"claude": ["--tools", "Read,Glob,Grep"]}'`.
    Without it, nothing starts (exit 2).
  - DIR is absolute. SECONDS is the shared deadline: 1–3500, default 1200.
- `agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH` is what each job runs. You do not
  call it. 0 only for DONE. Any other end (TIMEOUT, AUTH, CONTEXT, FAILED, INVALID_RESULT) is
  written to `status.json`, exit 3.
- `agent_compare` reads only `status.json` and `result.md`. No model. One packet ≤ 4096 bytes.

```text
# Ref: docs/delegation-plan.md §5 — the three calls
run(name="agent_ask", args=["--exec-args", POWERS, "both", "ask-race-1", "/abs/repo", "1200"],
    stdin=BRIEF)
run(name="agent_wait", args=["1200", "ask-race-1-claude", "ask-race-1-codex"], timeout=1230)
run(name="agent_compare", args=["ask-race-1-claude", "ask-race-1-codex"])
```

**Attended TUI (a human may step in):** `agent_start` → `tmux_wait` or `agent_wait` →
`agent_read`.

| Seed | argv | Exit codes |
|---|---|---|
| `agent_start` | `AGENT JOB DIR`, brief on stdin; AGENT is claude, codex or pi | 0 started (the brief landed) · 1 see below · 2 bad call · 127 tmux or the agent is missing |
| `agent_read` | `JOB [LINES]` (LINES 1–400, default 40) | 0 DONE or still live · 3 EXITED with no checked answer · 1 no such job, or not a delegate · 2 bad call |
| `agent_done` | `JOB` (the delegate runs this) | 0 passed: `status.json` written, signal sent · 1 refused, every problem listed · 2 bad call |

- `agent_start` exit 1: the job name is taken; the TUI exited at once; it wants a login (a human
  must do it); not ready in 90 s; or **`brief NOT taken`**. That last one means: after Enter, the
  screen showed no working mark and not the brief's first 40 chars, and codex sent no OSC
  `state=working`, within 5 s. A dialog probably ate the Enter. The window is left for a human.
- `agent_read` first line names the state: DONE, UNCHECKED, WORKING, NEEDS-INPUT, IDLE, EXITED.
  NEEDS-INPUT exits 0, like every live state.
- Follow-up and stop are borrowed from tmux: `tmux_send JOB TEXT` and `tmux_stop JOB`.
  There is no `agent_send` or `agent_stop`. A checked `result.md` outlives `tmux_stop`.
- Launch flags and the program path come only from `[delegates.AGENT]` (`args`, `bin`) in
  `~/.grimoire/config.toml`. Nothing in a brief can change them.

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
      Write your answer to <run_dir>/fix-auth/result.md with the headings above, then run:
      grim run agent_done -- fix-auth (it checks the file; fix what it reports).
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

  Scenario: A dialog eats the brief
    Given the TUI shows a dialog that takes the Enter
    When agent_start sends the brief
    Then it exits 1 with "brief NOT taken"
    And it never prints "started"

  Scenario: Collect the answer
    Given delegate "fix-auth" ran agent_done and it passed
    When the agent runs agent_read "fix-auth"
    Then it gets the text of result.md, not a screen scrape

  Scenario: The delegate forgot the contract
    Given the delegate went idle and wrote no result.md
    When the wait times out and the agent runs agent_read "fix-auth"
    Then it gets the pane text marked "no result.md"

  Scenario: The delegate needs input
    Given the delegate shows a question it cannot pass
    When the agent runs agent_read "fix-auth"
    Then the state is NEEDS-INPUT with the question text

  Scenario: Follow up
    Given delegate "fix-auth" is idle
    When the agent runs tmux_send "fix-auth" "now run the full suite"
    Then the text is typed and submitted in that pane

  Scenario: Stop a delegate
    Given delegate "fix-auth" is live
    When the agent runs tmux_stop "fix-auth"
    Then the window is gone
    And result.md is kept

  Scenario: A delegate's powers are stated
    When agent_start launches any delegate
    Then its permission mode is passed on the command line
    And the mode comes from config, or the delegate's own default if unset
    And neither the brief nor the driving agent can raise it

  Scenario: Ask two delegates headless
    When the agent runs agent_ask both "ask-1" with stated powers and a brief
    Then jobs "ask-1-claude" and "ask-1-codex" run, each on the same brief
    And agent_wait blocks once for both
    And agent_compare prints one packet with both verdicts
```

## Checked so far

- tmux 3.8, on an isolated server, 2026-11-12: `send-keys` text shows in `capture-pane`.
- From the library: `claude_tmux` has driven claude this way for 153 runs.
- Codex's OSC 7501 `state=working` was seen in a real codex log (2026-11-19 race). The brief-landed
  check on a real TUI is UNVERIFIED: only fakes have been driven.
