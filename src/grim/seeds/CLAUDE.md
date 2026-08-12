# Slice: seeds

## Purpose
Seed script bodies loaded on `grim init` (`shell`, `read_file`,
`write_file`, `edit_file`, `apply_patch`, `grep_tree`, `list_dir`,
`stats`, `gardener`, `export_library`, plus the background-job trio
`run_bg`/`list_bg`/`stop_bg` — build plan §3 Phase 3, D11). Meta-tooling ships as library scripts here,
not as new CLI verbs. The background trio tags detached processes
`grimbg:<name>` and tracks them under `$GRIM_RUN_DIR` (default
`~/.grimoire/run`): the answer to "long-lived work" is a background job,
not a longer `grim run` timeout (which is hard-capped, see verbs/run.py).

## Public interface
`loader.py` — `load_seeds(db) -> None`. The only function anything outside
this slice calls; individual seed bodies are data, not exported symbols.

## Invariants
- `load_seeds` is idempotent — safe to call against a database that
  already has these rows (re-running `grim init` must not duplicate or
  error). Idempotence is an explicit name lookup, never an exception
  swallow — any write_script rejection fails `grim init` loudly.
- Seeding ignores the language toggles (GRIM_LANGUAGES /
  GRIM_BASE_LANGUAGES): those gate agent writing, not the stdlib. The
  seed set is identical in every environment.
- Every seed is written with `seeded=1`, `scope='global'`.
- Seed bodies are plain scripts stored as `script_version.body` rows —
  this slice never gives seeds special execution privileges over
  agent-authored scripts.
