# Grimoire — QoL backlog

Quality-of-life enhancements imagined but not yet built. These are distinct
from the numbered build-plan phases (`docs/build-plan.md` §5) — those are big
features (TUI review queue, embeddings, draft bench, evals, the js/ts runner).
The items here are ergonomics, safety, and observability that mostly land as
**seeds or config keys**, not core changes.

Architectural note that shapes all of these: new *agent* capability grows as
seed scripts, never new verbs (D11); new *human* surface can be seeds too, so
the six-verb contract and the frozen kernel (`db.py`/`cli.py`/`config.py`) stay
untouched. Each item notes its likely home and rough effort.

## Config & environment (extends the shipped `config.toml`/env work)

- **More config keys** — add `GRIM_RUN_DIR` (already honored by the bg seeds)
  plus mini's `cost_limit`/`step_limit` (currently hardcoded in
  `grimoire.yaml`) to `config.py`'s `_CONFIG_ENV_KEYS`, so they're tunable
  without editing YAML. *Tiny; same pattern as the config bootstrap.*
- **Repo-local config layering** — `.grimoire/config.toml` in the repo, merged
  over the global one (like `.git`), for per-project model/timeout. Matches the
  D10 repo-scope model. *`config.py` gains one merge step (frozen — confirm).*
- **`grim config`** — print effective settings and each value's source (env vs
  file vs default), like `git config --list`. *Ships as a seed.*
- **`grim doctor`** (the planned Phase 4 `--check`) — verify uv/bash/rg/git
  present, FTS5 available, DB migrated, config parses. *Seed.*

## Observability (surface the execution log without Datasette)

- **`recent` seed** — terse CLI feed of the last N executions (script, exit,
  duration, session). *Pure read over the `execution` table.*
- **`stats --json` / `gardener --apply`** — machine-readable stats for
  dashboards; a human-gated flag so gardener can act on its archive proposals
  (today it only proposes). *Seed enhancements.*
- **Persist dup-pressure** — the write-time similarity nudge is printed but
  never recorded (build-plan §7 flags it as not-yet-computable). A
  `script.similarity_nudged` column or an event row unlocks the library-rot
  leading indicator. *Touches `verbs/write.py`.*

## Human authoring ergonomics

- **Shell completion** for script names (`grim run <TAB>`). *Generated
  completion script; names from `list`.*
- **`grim edit NAME`** — pull the latest body into `$EDITOR`, save → `update`.
  Beats heredocs. *Seed or console script.*
- **`grim rename`** (as a new version/alias) — naming drift is a named
  retrieval risk and there's no migration path today; the name *is* the API.

## Safety / robustness

- **Secret redaction in stored output** — stdout/stderr/argv are stored
  verbatim, so a key echoed by a script lands in the DB and Datasette. Add a
  redaction pass or a `--no-log` run flag. *Highest-priority safety gap given
  the persistence threat model (build-plan §8).*
- **Stored-output size cap** — full output goes into SQLite unbounded; the
  constitution's "explicit max on buffers" budget wants a cap here (store
  first/last N KB past a threshold, note the truncation).
- **Run confinement for untrusted repos** — env allowlist / network toggle /
  cwd jail, complementing the container.

## Agent-loop quality

- **Run-result cache *hint*** — when an identical script@version+argv ran
  seconds ago, lead the observation with "same run #NNNN exists (exit 0) —
  reuse via `read --exec`." A nudge, not a behavior change, so D8 (stateless)
  holds. Directly serves the compounding/token-saving thesis.
- **Container SIGTERM handling** (sibling of the process-group reaping fix) —
  make `docker stop` reap cleanly; optionally record `exit 130` for
  interrupted runs.

## Recommended first three

1. **Secret redaction / stored-output cap** — the item with a real safety edge;
   also satisfies a budget rule not currently met.
2. **`recent` + `grim config` seeds** — cheap, high daily-driver value, and a
   clean demonstration that observability grows in the data plane (D11).
3. **Run-result cache hint** — serves the compounding thesis at low risk.
