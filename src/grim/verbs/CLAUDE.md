# Slice: verbs

## Purpose
Implements the six agent-facing verbs — `write`, `update`, `read`, `list`,
`find`, `run` — against the schema in the root `CLAUDE.md` / build plan §3.
This is the entire closed set the agent (via `GrimEnvironment`) is allowed
to invoke; nothing here should assume a human-only caller.

## Public interface
One module per verb (`write.py`, `update.py`, `read.py`, `list.py`,
`find.py`, `run.py`), each exposing a single entry point dispatched by
`src/grim/cli.py`. No other module in this slice is part of the public
surface.

## Invariants
- Verb modules never import each other — a "find then run" flow composes
  at the CLI/adapter layer, not by one verb module calling another's
  function directly.
- All schema/execution I/O goes through `src/grim/db.py`; no verb opens
  its own SQLite connection.
- `run` dispatches through `src/grim/exec/`, never spawns a subprocess
  directly.
- `write`/`update` never skip the slug lint, mandatory-description check,
  or the FTS similarity nudge — those are the anti-duplication mechanism
  the build plan's §4 and §6 depend on.
- `write_script(..., enforce_language_gate=False)` bypasses only the
  env-derived writable-set check (for `grim init` seeding); the language
  must still exist in the runner catalog, and every other validation runs.
- `run` feeds the script's stdin by precedence: `--stdin-file`, then a
  piped/redirected (non-tty) sys.stdin read eagerly — the leg the adapter's
  run-tool `stdin` argument travels — and an interactive tty passes None.
  Tool-provided stdin is never silently dropped.
