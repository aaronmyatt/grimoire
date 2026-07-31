# Slice: adapter

## Purpose
Subclasses mini-swe-agent's environment so the model's action is parsed
and dispatched to `grim` in-process — the hard enforcement point for the
six-verb constraint (build plan D7, §2, Phase 2).

## Public interface
- `parse.py` — `parse_grim(text: str) -> ParsedCommand | None`. Pure
  text-in/data-out: shlex on the first non-blank line, optional trailing
  heredoc for the body, verb whitelisted against the six agent-facing
  verbs. A leading `grim` token is optional — models routinely drop it,
  treating the ` ```grim ` fence tag as already having said "grim" (the
  same convention every other language-tagged fence uses); both `grim
  <verb> ...` and bare `<verb> ...` are accepted. No mini-swe-agent
  import.
- `environment.py` — `GrimEnvironment`, subclassing mini-swe-agent's
  `LocalEnvironment` and overriding `execute(action: dict, cwd: str = "",
  *, timeout: int | None = None) -> dict[str, Any]` (the real upstream
  signature — mini's Model layer already extracted `action["command"]`
  from the fenced block before this is called). No shell runs in the
  control plane: `execute` calls `parse_grim`, then a private `_invoke`
  helper that runs `cli.main(argv)` **in-process** with redirected
  stdio — the only calls this module makes into the rest of the
  codebase. Reusing `cli.main()` (not a hand-rolled dispatcher) means
  `cli.py` needs zero changes for the agent path.
- `grimoire.yaml` — mini config carrying the `system_template` protocol
  ladder (build plan §6), `model.model_class: litellm_textbased` with a
  grim-specific `action_regex`, and `environment.environment_class:
  grim.adapter.environment.GrimEnvironment`. Data, not code — safe to
  hand-edit without touching `environment.py`.
- `streaming_model.py` — `GrimStreamingTextbasedModel` (build plan
  Phase 2b, optional). Same `LitellmTextbasedModel` contract, live
  terminal output instead of a blank pause per turn. Opt-in only: swap
  `model.model_class` in `grimoire.yaml` to
  `grim.adapter.streaming_model.GrimStreamingTextbasedModel`; the
  shipped default stays non-streaming.
- `run.sh` — the recommended launcher: `./run.sh -m <model> -y -t
  "<task>"`. A pre-launch wrapper only (runs *before* the agent loop
  starts, to pick a fresh `/tmp` trajectory path per invocation) — not
  part of the `execute()` control plane the invariants below govern, and
  not on the model's action path. Bypass it and call `uv run mini -c
  grimoire.yaml ...` directly for mini's stock single-fixed-file output
  behavior instead.

## Invariants
- Any action that doesn't parse as one of the six verbs (via
  `parse_grim`) returns the canned `PROTOCOL_REMINDER` observation —
  never falls through to actual execution.
- This module is the only caller of `cli.main()` on the agent's behalf;
  a human using the CLI directly invokes `grim`/`cli.py` themselves, not
  through here.
- No new shell/subprocess call is ever added to this slice — that would
  reopen exactly the bypass D7 exists to close.
- `_check_finished` is overridden (not inherited) because `run`'s
  observation always leads with a `[grim] exec #id...` header, so the
  submission sentinel is matched as any whole line of output rather than
  requiring it to be the literal first line.
