# Slice: seeds

## Purpose
Seed script bodies loaded on `grim init` (`run_cmd`, `run_py`, `shell`, `read_file`,
`write_file`, `edit_file`, `apply_patch`, `grep_tree`, `list_dir`,
`stats`, `gardener`, `export_library`, plus the background-job trio
`run_bg`/`list_bg`/`stop_bg` — build plan §3 Phase 3, D11). Meta-tooling ships as library scripts here,
not as new CLI verbs. The background trio tracks detached processes by pid
file under `$GRIM_RUN_DIR` (default `~/.grimoire/run`; each job also
carries `GRIMBG_NAME` in its env): the answer to "long-lived work" is a
background job, not a longer `grim run` timeout (which is hard-capped, see
verbs/run.py). Program-starting bodies live in `bodies_exec.py` and never
use a shell: argv only, shell operators and shell interpreters refused.

## Public interface
`loader.py` — `load_seeds(db) -> list[str]`: bare names for newly written
seeds, `name@version` for present-but-drifted seeds re-synced with the
bundled copies, `[]` when fully in sync. The only function anything
outside this slice calls; individual seed bodies are data, not exported
symbols.

## Invariants
- `load_seeds` is idempotent — safe to call against a database that
  already has these rows (re-running `grim init` must not duplicate or
  error). Idempotence is an explicit name lookup, never an exception
  swallow — any write_script rejection fails `grim init` loudly.
- `load_seeds` converges an existing library on this build's seed set: a
  present seed whose latest body differs from the bundled copy gets a new
  append-only version (via the update verb, changelog included — local
  divergence stays recoverable in history), and a drifted description is
  updated in place. Rows the human took over (`seeded=0`) or archived are
  never touched.
- Seeding ignores the language toggles (GRIM_LANGUAGES /
  GRIM_BASE_LANGUAGES): those gate agent writing, not the stdlib. Which
  seeds land is gated only by `GRIM_BASE_SEEDS` (unset = the full
  roster, so the default library floor is identical in every
  environment): a comma-separated subset of bundled names seeds — and
  converges — only those; explicitly empty seeds nothing (a control
  arm); an unknown name fails `grim init` loudly before any write. The
  knob gates seeding only — exclusion never removes or archives a seed
  already present.
- Every seed is written with `seeded=1`, `scope='global'`.
- Seed bodies are plain scripts stored as `script_version.body` rows —
  this slice never gives seeds special execution privileges over
  agent-authored scripts.
