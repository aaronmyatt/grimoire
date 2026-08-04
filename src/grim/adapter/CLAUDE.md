# Slice: adapter

## Purpose
Subclasses mini-swe-agent's model and environment so the agent acts through
native LLM tool calls that are dispatched to `grim` in-process — the hard
enforcement point for the six-verb constraint (build plan D7, §2, Phase 2;
D6 revised → native tool-calling).

## Public interface
- `tools.py` — `GRIM_TOOLS` (OpenAI function schemas for the six agent
  verbs + a terminal `submit` control) and `tool_call_to_argv(tool, args)
  -> (argv, stdin)`, the pure structured-args → `cli.main` argv mapper.
  Text/data in, data out; no mini-swe-agent import. This is the successor
  to the old text-based `parse.py`.
- `toolcall_model.py` — `GrimToolcallModel(LitellmModel)`. Overrides
  `_query` (hands the model `GRIM_TOOLS` instead of the single `bash`
  tool) and `_parse_actions` (validates each tool call — name + required
  args — into a `{tool, args, tool_call_id}` action, raising `FormatError`
  with a precise message on any deviation). Inherited
  `format_observation_messages` renders results as `role: "tool"` messages.
- `environment.py` — `GrimEnvironment`, subclassing mini-swe-agent's
  `LocalEnvironment` and overriding `execute(action, cwd="", *,
  timeout=None)`. No shell in the control plane: `execute` reads the
  structured `action["tool"]`/`action["args"]`, maps them via
  `tool_call_to_argv`, and runs `cli.main(argv)` **in-process** through the
  private `_invoke` helper (redirected stdio; traps argparse's
  `SystemExit` so a bad arg value returns as a nonzero observation, never
  a process kill). The `submit` tool raises `Submitted` directly — the
  deterministic stop. Reusing `cli.main()` means `cli.py` needs zero
  changes for the agent path.
- `grimoire.yaml` — mini config carrying the `system_template` protocol
  ladder (build plan §6), `model.model_class:
  grim.adapter.toolcall_model.GrimToolcallModel`,
  `environment.environment_class: grim.adapter.environment.GrimEnvironment`,
  and `agent.agent_class: grim.adapter.agent.GrimAgent`. Data, not code —
  safe to hand-edit without touching the Python.
- `agent.py` — `GrimAgent`, subclassing mini-swe-agent's
  `InteractiveAgent` to extend `run(task, **kwargs)`: before the first
  turn, it queries the script library's FTS5 index directly against the
  raw task text (own tiny tokenizer + query, not an import of
  `verbs/_shared.py` — slices don't share internals, root CLAUDE.md §2)
  and stashes only *strict* hits (`STRONG_MATCH_THRESHOLD`, bm25
  sign-flipped so higher = closer) into `extra_template_vars` as
  `grim_strong_matches`, which `system_template` renders conditionally.
  Mitigates build plan §8's "optional find misses/duplicates existing
  scripts" risk by surfacing a high-confidence hit before the agent
  decides whether to search at all. `run()` also stashes
  `user_prompt_extension()` (the operator's `~/.grimoire/system.md`, read
  fresh each run) as `grim_user_prompt`, which `system_template` renders as
  an `<operator_instructions>` block when non-empty — the agent-harness
  analogue of a global `~/.claude`/`~/.pi` instruction file. On `--continue`
  (`recall_enabled()` reads `GRIM_RECALL`), `run()` also stashes
  `recent_library(recall_limit())` as `grim_recent_library` — the agent's own
  recently-valuable scripts (non-seeded, run, not mostly-failing), value-ranked
  then ordered most-recent-**last**, rendered by `instance_template` beside the
  task (not the system prompt) so recency bias weighs it. Off by default: no
  `GRIM_RECALL` means the prompt is byte-for-byte unchanged.
- `run.sh` — the recommended launcher: `./run.sh -m <model> -y -t
  "<task>"`. A pre-launch wrapper only (runs *before* the agent loop
  starts, to pick a fresh `/tmp` trajectory path per invocation) — not
  part of the `execute()` control plane the invariants below govern.
  Bypass it and call `uv run mini -c grimoire.yaml ...` directly for
  mini's stock single-fixed-file output behavior instead.
- `completer.py` — `GrimCompleter` + `install_grim_completer()`. Attaches a
  prompt_toolkit completer to mini's own prompt sessions (no fork) so a human
  gets `@name` (library scripts + files) and `:name` (scripts only) completion
  while composing input. `GrimAgent.run` installs it; it's a no-op without a
  TTY. Completion-only — see `bang.py` for the `!`-execute affordance. The
  completed text is a plain mention; `grimoire.yaml` tells the agent what
  `@slug`/`:slug` (a script) vs `@path` (a file) mean.
- `bang.py` — `expand_bangs(text, runner) -> str` (pure over an injected
  runner) and `install_bang_expansion(session_id)`, Phase 2 of the
  @-command plan: `!slug`, as a standalone whitespace-bounded token, is
  replaced with `grim run slug`'s captured output before the text becomes a
  message. Wraps `.prompt` on the same two mini prompt sessions
  `install_grim_completer` reaches into, so every human-composed input is
  covered — the initial task, human-mode commands, interrupt comments,
  confirm/reject replies, and post-submit new tasks. Execution goes through
  `environment._invoke` (in-process `cli.main`, no new subprocess); an
  unknown slug isn't special-cased — whatever `grim run` prints, success or
  its own error, is exactly what gets substituted. Bounded to
  `MAX_BANGS_PER_MESSAGE` per message. `GrimAgent.run` installs it.
- `launcher.py` — `main()`, the `grim-agent` console-script entry (the
  installable yolo harness). Parses grim's own flags — `-p`/`--print` and
  `--output-format text|json` for a clean, pipeable one-shot (mini's UI to
  stderr, only the final `submit` result to stdout, `--exit-immediately`
  forced) — then hands the remaining argv to mini's Typer app in-process.
  `parse_result`/`summarize_run`/`format_output` are pure helpers over mini's
  trajectory `info` block, unit-tested without a model. `--continue` turns on
  library recall (sets `GRIM_RECALL`) and reuses the last agent session's id
  (`last_agent_session_id` -> `-c environment.session_id=…`) so executions
  extend that lineage; the flag is stripped before dispatch (`_take_flag`).

## Invariants
- The model may only call the tools in `GRIM_TOOLS`; `GrimToolcallModel`
  rejects anything else (unknown tool, missing/invalid args) with a
  `FormatError` — an action never falls through to unvalidated execution.
- Task completion is *only* the `submit` tool call, which raises
  `Submitted` with the model's `result`. There is no output-sentinel scan
  — a script may freely print any text, including old sentinels.
- This module is the only caller of `cli.main()` on the agent's behalf; a
  human using the CLI directly invokes `grim`/`cli.py` themselves, not
  through here.
- No new shell/subprocess call is ever added to this slice — that would
  reopen exactly the bypass D7 exists to close.
- `-p`/`--output-format`/`--continue` are the human launcher's flags only:
  `parse_print_options`/`_take_flag` strip them from argv before dispatch, so
  the model's argv (and the six-verb contract, D12) is never touched.
