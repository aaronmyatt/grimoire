# Grimoire (`grim`)

A script-hoarding agent harness on [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent).

## Vision

Grimoire replaces mini-swe-agent's raw-bash action space with six verbs
over a SQLite spine: `write`, `update`, `read`, `list`, `find`, `run`.
Every action the agent takes becomes a named, versioned artifact with
recorded input/output. The agent's memory *is* its executable library —
each accumulated script is a new verb in its vocabulary, and `find` is how
it remembers it knows the word. Humans and the agent share the same
binary and the same database.

The thesis being tested: bash is great, the entire corpus of programming
languages is better — and a persistent, searchable corpus of the agent's
own scripts compounds, saving tokens on repeat work and leaving the human
a usable library behind.

Bash isn't banned — it's demoted. `sh` (run one shell command) ships as
just another seeded script in the corpus, not a privileged escape hatch.

## Architecture

```
┌─────────────────────────────┐
│ mini-swe-agent (unmodified) │  DefaultAgent · linear history · cost limits
│   └── GrimEnvironment ──────┼──► parses action → calls grim lib in-process
├─────────────────────────────┤
│ grim  (CLI + library)       │  six verbs · init/doctor/draft for humans
│   ├── verbs/                │
│   ├── exec/  (dispatch)─────┼──► uv run | bash | bun | run --json
│   └── db.py  (schema/query) │
├─────────────────────────────┤
│ grimoire.db (SQLite)        │  scripts · versions · executions · sessions · FTS
├─────────────────────────────┤
│ seed library                │  sh, read_file, apply_patch… + stats/gardener/export
├─────────────────────────────┤
│ human surfaces              │  same CLI · Datasette · fzf one-liner · TUI (review queue)
└─────────────────────────────┘
```

Every script write is versioned (append-only — `update` never overwrites,
it adds a version); every run is logged as an `execution` row keyed to
the exact version that produced it. Nothing is imperatively wired between
scripts — "called before/after," success rate, staleness, and so on are
all views derived from that log, not bookkept relationships.

## Slices

The codebase is vertical slices under `src/grim/`. A slice is
self-contained, never imports another slice, and is reached only through
its public entry point. Duplication between slices is a deliberate
choice, not debt — see `ABSTRACTIONS.md` for flagged (not yet acted on)
extraction opportunities. Each slice's own `CLAUDE.md` is the source of
truth for its invariants; this is a summary.

| Slice | Purpose | Public interface |
|---|---|---|
| **`db.py` / `cli.py` / `config.py`** (shared kernel, frozen) | SQLite connection, PRAGMAs, migration runner; argparse dispatch; global-config → env-var defaults | `connect`, `migrate`, `init_db` · `build_parser`, `main` · `apply_global_config` |
| **`verbs/`** | The six agent-facing verbs — the entire closed set the agent may invoke | one module per verb (`write.py`, `update.py`, `read.py`, `list.py`, `find.py`, `run.py`) |
| **`exec/`** | Language dispatch table (`python → uv run`, `bash → bash`, `js/ts → bun`, else → `run --json`) and output truncation | `dispatch.dispatch(...)`, `envelope.truncate(...)` |
| **`adapter/`** | Subclasses mini-swe-agent's environment so model actions are parsed and dispatched to `grim` in-process — the hard enforcement point for the six-verb constraint | `environment.GrimEnvironment`, `grimoire.yaml` |
| **`seeds/`** | Seed script bodies loaded on `grim init` (`sh`, `read_file`, `apply_patch`, `stats`, `gardener`, …) — meta-tooling ships as library scripts, never new CLI verbs | `loader.load_seeds(db)` |

The shared kernel (`db.py`, `cli.py`, `config.py`) is tiny, frozen, and
versioned like a third-party library: slices depend on it or on nothing,
never on each other. Within a slice, plain function calls; across slices, only the URL,
the parent process, or a message across a time boundary — never an
in-process event bus.

## Checks & constraints

This repo is built around hard budgets, enforced mechanically rather than
by convention (full detail and current numbers: root `CLAUDE.md`,
`.claude/budgets.json`).

| Budget | Limit |
|---|---|
| Function length | ≤ 60 lines |
| File length | ≤ 500 lines |
| Change size (diff) | ≤ 300 changed lines |
| Params per function | ≤ 4 |
| Nesting depth | ≤ 4 |
| Cyclomatic complexity | ≤ 10 |
| Line width | ≤ 100 cols |
| Assertions per function | ≥ 2 |
| Coverage | never regresses |

Enforcement is three hook layers, none of them vibes-based (see
`.claude/hooks/`):

- **`fence.sh`** (before an edit) — blocks edits outside the current
  slice, and either asks or flatly denies writes to frozen/baseline
  paths. The active slice is inferred from the working tree's own diff:
  the first uncommitted write to a slice claims it; a write to a
  different slice is denied until that one is committed or discarded.
- **`feedback.sh`** (after an edit) — formats, lints, and typechecks the
  file just touched; informs, never blocks.
- **`gate.sh`** (before ending a turn) — blocks until formatter, linter,
  and typechecker are clean, the test suite is green, and the diff is
  within budget.

**Frozen paths** (`src/grim/db.py`, `src/grim/cli.py`, `src/grim/config.py`,
`.claude/**`, `CLAUDE.md`, `pyproject.toml`, `uv.lock`,
`.github/workflows/**`) are
human-confirmed on every single write, individually, forever — they're
never edited as a side effect of slice work. `.claude/mypy-baseline.txt`
is stricter still: it's a tool-generated baseline, denied outright for
hand edits, regenerated only by piping the real tool's output.

Debt is tracked, not hidden: pre-existing violations are baselined at
setup time and burned down deliberately via `/ratchet` (one rule × one
slice per session), never fixed as a drive-by inside unrelated work. The
ledger lives in `RATCHET.md`.

## Getting started

```bash
uv sync
uv run grim init      # creates ~/.grimoire/grimoire.db (or $GRIM_DB) and applies schema v1
uv run grim completion  # (re)install bash+zsh tab-completion — `grim run <TAB>` reads script names from the db (auto-runs on init)
uv run pytest         # smoke tests: fresh init + idempotent re-init
```

## Global config

Persistent defaults live in `~/.grimoire/config.toml` (TOML). Each key seeds
the matching environment variable via `os.environ.setdefault`, so **a value
exported in your shell always wins** — precedence is shell env → config file →
built-in default. Missing or malformed files are ignored with a warning.

```toml
# ~/.grimoire/config.toml
model    = "anthropic/claude-sonnet-4-5"   # -> GRIM_MODEL   (grim-agent's model)
timeout  = 300                             # -> GRIM_TIMEOUT (grim run default, capped at 3600s)
# db     = "/path/to/grimoire.db"          # -> GRIM_DB
# traj_dir = "/path/to/trajectories"       # -> GRIM_TRAJ_DIR
```

## Install as a CLI harness

For your own machine, install grim as a standalone yolo-mode agent harness
(like `mini` or `pi`) — one command runs the autonomous loop, no repo
checkout required. The `[agent]` extra pulls in mini-swe-agent:

```bash
uv tool install "grimoire[agent]"

# GRIM_MODEL/-m picks the model; litellm reads the matching <PROVIDER>_API_KEY
# from the environment itself. The first bare argument is the task.
grim-agent "summarize README.md" -m anthropic/claude-sonnet-4-5
```

This installs two commands from one package: `grim` (the library CLI — the
six verbs plus `init`/`doctor`) and `grim-agent` (the harness launcher). The
run is fully unattended (`-y --exit-immediately`): no per-action confirmation,
and it exits on `submit` instead of prompting for a new task. Each run writes
a trajectory JSON under `$GRIM_TRAJ_DIR` (default the system temp dir).

> **Safety.** `grim-agent` executes model-authored scripts directly on your
> host with no confirmation — fine for your own tasks, not for untrusted
> repos. For isolation (and for eval runs), use the container below.

## Run in a container

A self-contained image runs arbitrary LLM prompts through the grim adapter.
The model id and provider API key come from the environment at run time —
nothing is baked in.

```bash
docker build -t grimoire .

# Run a prompt. GRIM_MODEL picks the model; litellm reads the matching
# <PROVIDER>_API_KEY from the env itself. Mount a volume at /data to keep
# the accumulated library between runs.
docker run --rm \
  -e GRIM_MODEL=anthropic/claude-sonnet-4-5 \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -v "$HOME/.grimoire:/data" \
  grimoire "summarize README.md"
```

The run is fully unattended (`--exit-immediately -y`): no per-action
confirmation, and it exits on `submit` instead of prompting for a new task.

The same image doubles as the plain CLI when the first argument is a known
binary — no model needed:

```bash
docker run --rm grimoire grim list
docker run --rm grimoire grim run shell -- echo hi
```

**Verify the image without an API key.** `docker/smoke.sh` builds the image
and checks the non-LLM surface end to end (seeds load, a python script
dispatches, the entrypoint fails fast when `GRIM_MODEL` is unset):

```bash
./docker/smoke.sh
```

## Human surfaces

The whole library is one SQLite file, so the human surfaces are just
different lenses over it — no separate store to keep in sync (Phase 5a).

**Browse it (Datasette).** Canned queries — recent executions, top scripts
by use, the failure feed, and the emergent-pipeline affinity view — ship in
`surfaces/datasette/metadata.json`:

```bash
uvx datasette ~/.grimoire/grimoire.db -m surfaces/datasette/metadata.json
# open http://127.0.0.1:8001 → "Queries" (the -m db key is the file stem,
# "grimoire"; point it elsewhere if you run with a non-default $GRIM_DB)
```

**Pick + preview (fzf).** A one-liner turns `grim list` into a fuzzy picker
with the script's source in the preview pane:

```bash
grim list | fzf --preview 'grim read {1}'
```

**Snapshot it to disk.** The `export_library` seed dumps the latest version
of every non-archived script to a git-friendly tree:

```bash
grim run export_library -- ./library-export
```

## Working in this repo

Root `CLAUDE.md` is the constitution (budgets, slice rules, change
protocol, commit discipline) — read it before making a change of any
size. Each slice's own `CLAUDE.md` (`src/grim/<slice>/CLAUDE.md`) states
that slice's purpose, public interface, and invariants; read it before
touching that slice.
