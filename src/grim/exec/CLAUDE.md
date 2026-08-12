# Slice: exec

## Purpose
The language dispatch table and execution envelope: `python → uv run`,
`bash → bash`, plus an opt-in catalog of extended languages
(docs/languages.md) enabled via config.toml's `[languages]` table — all
off by default, platform-gated where needed (osascript → macOS only).
Owns truncation of stored output into the observation returned to the
agent (build plan D8, D9, §4).

## Public interface
- `dispatch.py` — `dispatch(script_version: ScriptVersion, request:
  ExecutionRequest) -> ExecutionResult`. `ExecutionRequest` bundles
  `argv`/`stdin`/`cwd`/`timeout` (root CLAUDE.md §3: >4 params bundle
  into an options struct). The only entry point the `verbs/run.py`
  module calls. Bash + python always run; a catalogued extended language
  runs even when disabled in config (the toggle gates writing, not
  executing the library), and `supported_languages()` — the subsettable
  builtins (`base_languages()`, from `GRIM_BASE_LANGUAGES`: unset -> both,
  '' -> none — the solo-language experiment knob) plus enabled,
  platform-valid extended — is the write-time gate, with a fail-safe:
  if both knobs empty the set, it falls back to the builtin pair.
- `envelope.py` — `truncate(stdout, stderr, ...) -> str`, the
  first-40/last-10-lines formatting described in build plan §4.

## Invariants
- Stateless: a call is a pure function of `(script_version, argv, stdin)`
  plus a timeout — no execution-layer caching or session state lives here.
  The `execution` table (owned by `db.py`) is the only persistence.
- Every execution records an `env_fingerprint` (interpreter versions) —
  required for the staleness triage the build plan's gardener depends on.
- A timeout always kills the subprocess **process group** (the child runs
  with `start_new_session`, so its grandchildren die too — no orphans) and
  records exit code 124; never hangs the caller. A `KeyboardInterrupt`
  during a run kills the same group and re-raises, so the harness's
  two-press Ctrl-C (cancel the run, then exit) stays intact.
- A child that reads stdin always reaches EOF: provided stdin travels via
  PIPE; no stdin means DEVNULL (instant EOF); the caller's fd is inherited
  only when it is a real interactive tty. Harness-internal system calls
  (spawn, version probe) are hard-bounded and fail loudly when exceeded —
  a slow platform is never absorbed into a script's wall time.
