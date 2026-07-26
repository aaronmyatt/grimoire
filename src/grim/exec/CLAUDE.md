# Slice: exec

## Purpose
The language dispatch table and execution envelope: `python → uv run`,
`bash → bash`, `js/ts → bun`, everything else → `run --json`
(Esubaalew/run). Owns truncation of stored output into the observation
returned to the agent (build plan D8, D9, §4).

## Public interface
- `dispatch.py` — `dispatch(script_version, argv, stdin, cwd, timeout) ->
  ExecutionResult`. The only entry point the `verbs/run.py` module calls.
- `envelope.py` — `truncate(stdout, stderr, ...) -> str`, the
  first-40/last-10-lines formatting described in build plan §4.

## Invariants
- Stateless: a call is a pure function of `(script_version, argv, stdin)`
  plus a timeout — no execution-layer caching or session state lives here.
  The `execution` table (owned by `db.py`) is the only persistence.
- Every execution records an `env_fingerprint` (interpreter versions) —
  required for the staleness triage the build plan's gardener depends on.
- A timeout always kills the subprocess and records exit code 124 — never
  hangs the caller.
