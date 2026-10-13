# Slice: metrics

## Purpose
`grim stats` — raw usage numbers from the library (`grimoire.db`) and the
span log (`instrumentation.jsonl`), printed in machine and human formats so
a later visualisation layer (or the agent itself) can chart them. Answers
the scorecard in `docs/metrics-research.md`. Human-facing like the curate
commands: never on the agent's tool surface (`adapter/tools.py`).

## Public interface
- `command.configure(parser)` — adds `grim stats` arguments to its
  subparser and dispatches to `command.cmd_stats(args) -> int`. `cli.py`
  calls `configure`; nothing else outside the slice imports from here.
- `grim stats [DATASET] [--format FMT] [--since YYYY-MM-DD]`:
  - DATASET (`datasets.DATASETS`): `summary` (default, one all-time row),
    `weekly` (one row per ISO week), `sessions`, `runs`, `scripts` (raw,
    one row per thing). Columns: `periods.PERIOD_COLUMNS`,
    `sessions.SESSION_COLUMNS`, `datasets.RUN_COLUMNS`/`SCRIPT_COLUMNS`.
  - FMT (`formats.FORMATS`): `table` (default), `json` (envelope with
    `dataset`, `generated_at`, `since`, `columns`, `rows`), `jsonl`, `csv`,
    `tsv`, `md`.
  - Exit 2 for a bad `--since`, 1 when no library exists, 0 otherwise.

## Invariants
- Read-only: no dataset build writes to the library (asserted in tests via
  `total_changes`); the span log is only ever read.
- Machine formats (json, jsonl, csv, tsv) carry every value untouched;
  only human formats (table, md) shorten cells, to `TABLE_CELL_MAX_CHARS`.
- Every row of a dataset has exactly that dataset's columns, in order.
- Reuse = a run of a NON-seed script written in another session. Seeds
  carry origin `human-adhoc`, so without `seeded = 0` every seed run would
  count as reuse (the inflated-reuse bug the stats seed still has).
- Escape hatch = `shell`, `run_sh`, `run_cmd`, `run_py`
  (`queries.ESCAPE_HATCHES`, duplicated in the `stats` seed and
  `evals/harness_health.py` — flagged in `ABSTRACTIONS.md`).
- The span log path (`$GRIM_INSTRUMENT_FILE`, else
  `~/.grimoire/instrumentation.jsonl`) duplicates adapter/trace.py's; a
  missing or unreadable log degrades to no span columns, never a crash.
- Timestamps are SQLite's `YYYY-MM-DD HH:MM:SS` UTC text everywhere, span
  times included (`spans._utc`), so rows sort and bucket together.
