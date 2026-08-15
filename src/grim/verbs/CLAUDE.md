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
- `script.scope` is `'global'` or the enclosing repo's 12-hex root-commit
  id (`_shared.repo_identity` — worktree/clone-stable, unlike a path
  hash; `SCOPE_RE` is the shape contract). `write` resolves the tool
  literal `'repo'`/`None` via `_shared.resolve_scope`, rejects anything
  else out of shape, and stamps repo-scoped scripts with a human-readable
  `repo-<toplevel-basename>` provenance tag (curate's tag tables;
  duplication flagged in `ABSTRACTIONS.md`). `find` tiers results by
  provenance — current repo, then global, then foreign/legacy scopes —
  before bm25, so cross-repo noise never outranks the working repo's own
  scripts.
- `write_script(..., enforce_language_gate=False)` bypasses only the
  env-derived writable-set check (for `grim init` seeding); the language
  must still exist in the runner catalog, and every other validation runs.
- `run` feeds the script's stdin by precedence: `--stdin-file`, then a
  piped/redirected (non-tty) sys.stdin read eagerly — the leg the adapter's
  run-tool `stdin` argument travels — and an interactive tty passes None.
  Tool-provided stdin is never silently dropped.
- `run` clamps each captured stream to `STORED_STREAM_MAX_CHARS` before
  the execution INSERT (head + tail kept, middle elided with a sized
  marker), and builds the observation from the same clamped text — no
  single stored value can exceed the budget, and what the agent read is
  what `grim read --exec` replays. The 2026-08-15 regression: an
  unclamped ~1 GB stdout hit SQLITE_TOOBIG at the INSERT and the
  resulting DataError killed the session.
- `run` pins each dispatched script's working directory to `$GRIM_CWD`
  when set (the adapter exports it around every in-process verb call —
  see `run.cwd_from_env`), and records it on the execution row. Unset —
  the human-CLI path — means None: the subprocess inherits the shell's
  cwd, unchanged. A relative or nonexistent value degrades to None,
  never crashes the run.
