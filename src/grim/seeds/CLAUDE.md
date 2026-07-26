# Slice: seeds

## Purpose
Seed script bodies loaded on `grim init` (`sh`, `read_file`, `write_file`,
`apply_patch`, `grep_tree`, `list_dir`, `stats`, `gardener`,
`export_library` — build plan §3 Phase 3, D11). Meta-tooling ships as
library scripts here, not as new CLI verbs.

## Public interface
`loader.py` — `load_seeds(db) -> None`. The only function anything outside
this slice calls; individual seed bodies are data, not exported symbols.

## Invariants
- `load_seeds` is idempotent — safe to call against a database that
  already has these rows (re-running `grim init` must not duplicate or
  error).
- Every seed is written with `seeded=1`, `scope='global'`.
- Seed bodies are plain scripts stored as `script_version.body` rows —
  this slice never gives seeds special execution privileges over
  agent-authored scripts.
