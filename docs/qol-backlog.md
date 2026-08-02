# Grimoire — QoL backlog

Quality-of-life enhancements — the "Config & environment" group is shipped
(see below); the rest are imagined but not yet built. These are distinct from
the numbered build-plan phases (`docs/build-plan.md` §5) — those are big
features (TUI review queue, embeddings, draft bench, evals, the js/ts runner).
The items here are ergonomics, safety, and observability that mostly land as
**seeds or config keys** (a few, like diagnostics, as in-process kernel
commands), not core changes.

Architectural note that shapes all of these: new *agent* capability grows as
seed scripts, never new verbs (D11); new *human* surface can be seeds too, so
the six-verb contract and the frozen kernel (`db.py`/`cli.py`/`config.py`) stay
untouched. Each item notes its likely home and rough effort.

## Config & environment — ✅ DONE

Shipped as a group. Correction applied during the build: `grim doctor` and
`grim config` are **in-process kernel subcommands (`cli.py`), not seeds** — a
seed runs via `grim run`, which needs a migrated DB and working dispatch (`uv`),
i.e. exactly what those diagnostics exist to check. A seed can't diagnose the
substrate it runs on, so both are exempt from the database-ready gate.

- ✅ **More config keys** — `run_dir`/`cost`/`step` → `GRIM_RUN_DIR`/
  `GRIM_COST_LIMIT`/`GRIM_STEP_LIMIT` in `config.py`. Cost/step also needed
  launcher wiring (`-l`, `-c agent.step_limit=N`) — mini only sees them via the
  launcher, not `config.py` alone.
- ✅ **Repo-local config layering** — nearest `./.grimoire/config.toml`
  (bounded git-style walk-up, excluding the global) layered over the global:
  precedence **shell env > repo > global > default**, all via `setdefault`.
- ✅ **`grim config`** — `config.effective_config()` + `cmd_config` print each
  key, value, and source (env/repo/global/default); works before `grim init`.
- ✅ **`grim doctor`** — in-process checks (uv/bash required; rg/git optional;
  FTS5 probe; DB state; config parse); exits nonzero only on a critical
  failure. (Distinct from build-plan §5 Phase 4's `init --check`, which shells
  out to the `run` tool's toolchain doctor once the substrate is healthy.)

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
