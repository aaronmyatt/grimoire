# Slice: adapter

## Purpose
Subclasses mini-swe-agent's model and environment so the agent acts through
native LLM tool calls that are dispatched to `grim` in-process — the hard
enforcement point for the six-verb constraint (build plan D7, §2, Phase 2;
D6 revised → native tool-calling).

## Public interface
- `tools.py` — `GRIM_TOOLS` (OpenAI function schemas for the six agent
  verbs + a terminal `submit` control), `tool_call_to_argv(tool, args)
  -> (argv, stdin)`, the pure structured-args → `cli.main` argv mapper,
  and `lang_enum()` — the enabled-language list ($GRIM_LANGUAGES extended
  + $GRIM_BASE_LANGUAGES-subsettable builtins, never-empty fail-safe,
  mirroring exec/dispatch.py's knob pair) that feeds BOTH the write/list
  schema enums and the prompt (GrimAgent stashes it as `grim_languages`),
  so schema and prose can never drift. Text/data in, data out; no mini-swe-agent import. This is
  the successor to the old text-based `parse.py`.
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
  `tools.lang_enum()` as `grim_languages`, rendered by BOTH templates so
  every enabled language is named in prose (static python-or-bash fallback
  when undefined — the language-sweep confound fix), and
  `user_prompt_extension()` (the operator's `~/.grimoire/system.md`, read
  fresh each run) as `grim_user_prompt`, which `system_template` renders as
  an `<operator_instructions>` block when non-empty — the agent-harness
  analogue of a global `~/.claude`/`~/.pi` instruction file. On `--continue`
  (`recall_enabled()` reads `GRIM_RECALL`), `run()` also stashes
  `recent_library(recall_limit())` as `grim_recent_library` — the agent's own
  recently-valuable scripts (non-seeded, run, not mostly-failing), value-ranked
  then ordered most-recent-**last**, rendered by `instance_template` beside the
  task (not the system prompt) so recency bias weighs it. Off by default: no
  `GRIM_RECALL` means the prompt is byte-for-byte unchanged. `GrimAgent` also
  overrides `_prompt_and_handle_slash_commands` to extend mini's own `/h /m
  /y /c /u`: `/verb ...` dispatches grim's own CLI verbs (`slash.py`) and
  `/new <task>` starts a fresh session — see both below. `run()` itself loops
  over `InteractiveAgent.run()` (rather than looping inside it) so `/new` can
  break all the way out via an exit-flavored `UserInterruption` and restart
  cleanly with a rotated `env.session_id`, instead of resetting state
  mid-flow. `GrimAgent` also overrides `add_messages` to render grim tool
  calls via `display.py` (submit → Markdown, verbs → their `render_command`
  line) instead of mini's raw-JSON fence; history is appended verbatim
  through `DefaultAgent.add_messages` — only the console rendering changes.
- `display.py` — pure renderable helpers for `add_messages`:
  `grim_actions(message)` (validated `{tool, args, command}` actions or
  None → mini's default rendering), `submit_result`, `reasoning_text`
  (content minus `tool_calls`, so the raw-JSON fallback is never reached),
  `body_lexer`, and `action_renderables` (submit → `Rule` + `Markdown`;
  data verbs → escaped `$ grim …` line + a `BODY_PREVIEW_LINES`-capped
  `Syntax` body preview for write/update). Display only — actions,
  confirm-mode matching, and the trajectory are untouched.
- `slash.py` — `GRIM_CLI_VERBS` (a small, intentional duplicate of
  `cli.py`'s subcommand names — `init`/`config`/`doctor`/`near`/`recent`/
  `edit`/`tag`/`untag`/`tags`/`tagged`/`favourite`/`unfavourite`/
  `favourites`/the six data verbs) and `run_slash_command(text, session_id)
  -> str | None`. `/verb args...` dispatches straight to `cli.main` in-process
  via the same `environment._invoke` bang.py uses — a human side-channel,
  never sent to the model or billed as tokens, the same category as mini's
  own `/h`. Recognizes `/edit NAME` too: since `_invoke` only swaps the
  Python-level `sys.stdin`/`stdout`, not the OS file descriptors, `grim
  edit`'s `$EDITOR` subprocess (inherited stdio, curate/CLAUDE.md) still
  gets the real terminal — the same verb code works from a real shell and
  from this slash command.
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
- Known tools validate against their `GRIM_TOOLS` schemas. An unknown
  tool NAME is library fallthrough: `_lower_script_call` rewrites it to
  `run(name=<tool>)` (run's own keys pass through; other scalar values
  become argv in call order) and the result is validated exactly like an
  explicit run call. Malformed input (non-JSON args, non-scalar lowering
  values, missing/invalid args on known tools) still raises `FormatError`
  — an action never reaches execution unvalidated.
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
