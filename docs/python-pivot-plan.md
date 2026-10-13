# Plan — logging gaps, harness fixes, Python-only pivot

Status: **draft, awaiting approval** (root CLAUDE.md §4: plan before code).
Evidence: `~/.grimoire/grimoire.db` (235 agent sessions, 10,947 runs) and
`instrumentation.jsonl` (103 traced sessions), analysed 2026-10-13.

## 1. Why

| Signal | Now | Target |
|---|---|---|
| Runs that exit nonzero | 13.1% (bash 22%, python 12%) | < 8% |
| Runs that are `shell` / `run_sh` (bash) | 65% | 0% of new runs |
| Format-error spans with a cause | 0 of 248 | 100% |
| `session.model` / `task` filled | 0 of 236 rows | 100% |
| Trajectories kept | 1 (rest lost in `/tmp`) | last 500 |
| Agent scripts used in ≤ 1 session | 76% | `scratch_` absorbs one-offs |
| Prompt size, Oct p90 | 372k tokens | ≤ 120k |

Python failures the pivot must also address (non-shell python runs, 499 fails):
nonzero with empty stderr 186 · `FileNotFoundError` 58 · usage/exit 2 54 ·
`ModuleNotFoundError` 24.

## 2. Decisions (picked, with the option not taken)

| # | Question | Picked | Not taken | Why |
|---|---|---|---|---|
| D1 | What replaces `shell`? | `run_cmd` (one program, argv list, no shell) + `run_py` (Python snippet on stdin) | `run_py` only | git/ssh/curl/docker are ~600 runs; one call beats a subprocess wrapper |
| D2 | Legacy bash scripts (252 live) | Stay runnable; **port on touch** (update forces `--lang python`); archive unported after 30 days | Bulk-port now | No big-bang diff; reuse decides what's worth porting |
| D3 | Extended languages (opt-in jq, ruby…) | Off for writing too — python only | Keep opt-in | "Exclusively python" |
| D4 | Scratch tier | `scratch_` name prefix | `--scratch` flag + tag | Flag needs frozen `cli.py`; prefix needs no schema or CLI change |
| D5 | Nudge for `cat`/`grep`/`ls`/`sed` | Soft hint on stderr, still runs | Refuse with exit 2 | Refusing adds failures; measure first, harden later |
| D6 | Order | Logging → baseline A/B → fixes → pivot | Pivot first | Can't prove the pivot helped without the logs |
| D7 | Old fix #2 (shell → bash) | Dropped — `shell` retires | — | Superseded |
| D8 | Old fix #4 (PATH shims) | Replaced by a `run_cmd` hint when argv[0] is a library script | Shim dir on PATH | 127s came from `shell`; with it gone a hint is enough |

Scope boundary: "no bash" means the **agent's** action space and library.
Dev tooling (`run.sh`, `docker-entrypoint.sh`, `.claude/hooks/*.sh`) is out of scope.

## 3. Constraints that shape the task list

- One slice per task, ≤ 300 changed lines, one concern per commit.
- Behavior changes get characterization tests first if current behavior is unpinned.
- Already over the 500-line cap: `adapter/context.py` (501), `adapter/launcher.py`
  (582), `seeds/bodies.py` (505). Any task touching them is preceded by a
  behavior-preserving split (its own commit), or is a net-zero line swap.
- No frozen-path writes. No new dependencies (stdlib only).

## 4. Phase 0 — Logging (fix 1, priority)

### L1 · adapter · error text on spans
- `trace.py`: span's except-branch records `error_msg` beside `error`.
- `def _error_text(exc: BaseException) -> str` — FormatError carries a message
  dict; use its `content`, else `str(exc)`. Cap `ERROR_TEXT_LIMIT_CHARS = 300`.
- Invariants: never raises (degrade-don't-crash); `len ≤ 300`; `error` field unchanged.
- Tests: `test_trace.py` — FormatError → `error_msg` present, capped; plain exception too.
- ~40 lines.

### L2 · adapter · return code on tool spans
- `environment.execute`: `sp.update(returncode=out["returncode"])` after `_execute`.
- Invariants: every non-submit `tool.*` span has an int `returncode`.
- Tests: `test_environment.py` with `GRIM_INSTRUMENT=1` + temp file.
- ~30 lines.

### L3 · verbs · session row metadata
- `ensure_session(conn, session_id)` fills `model`, `task` (≤ 500 chars) from
  `GRIM_SESSION_MODEL` / `GRIM_SESSION_TASK`, and `repo_fingerprint` from the
  existing scope helper, on first insert.
- Invariants: never overwrites a non-NULL column; `human-adhoc` keeps NULL model.
- Tests: `test_shared.py` — with/without env; second call doesn't overwrite.
- ~50 lines.

### L4 · adapter · export session metadata
- `_session_env` exports the two vars next to `GRIM_SESSION` / `GRIM_CWD`
  (same cross-slice channel as today). `GrimAgent.run` stashes model + task on env.
- Depends on L3. ~30 lines.

### L5 · adapter · keep trajectories
- New `adapter/trajectories.py`:
  - `def trajectory_dir() -> Path` — `$GRIM_TRAJ_DIR`, else `~/.grimoire/trajectories`.
  - `def prune(directory: Path, keep: int) -> int` — delete oldest `*.traj.json`
    beyond `GRIM_TRAJ_KEEP` (default 500). Returns count deleted.
- `launcher.py`: one-line swap to call it (net ≤ 0 lines). `run.sh`: same dir.
- Invariants: only `*.traj.json` ever deleted; `keep ≥ 1`; path ends `.traj.json`.
- **Human edit:** delete `traj_dir="/tmp"` from `~/.grimoire/config.toml`.
- ~120 lines incl. tests.

### L6 · adapter · close reason
- `trace.session_close(exit_status=…, llm_calls=…)` from `GrimAgent.run`'s
  `finally` (mini's exit status, or the exception class name).
- `atexit` closes a still-open session with `exit_status="process_exit"`
  (covers the 8 sessions with no close record; SIGKILL stays invisible).
- ~50 lines.

### L7 · evals · checked-in health report
- `evals/harness_health.py` (stdlib): prints the §1 table from the DB + jsonl,
  top-10 format-error messages, and **asserts** L1–L4 invariants on sessions
  newer than `--since`.
- This is the before/after ruler for every later phase. ~180 lines.

## 5. Phase 1 — Baseline A/B (no code)

Run with the existing knobs before changing behavior:

```sh
# Arm A — today's harness
smevals run evals/grim-debug ; smevals run evals/grim-solve
# Arm B — python-only, no shell seed (worst case: run_cmd/run_py don't exist yet)
export GRIM_EVAL_BASE_LANGUAGES=python GRIM_EVAL_LANGUAGES=python
export GRIM_BASE_SEEDS=read_file,write_file,edit_file,apply_patch,grep_tree,list_dir,stats,gardener,export_library,run_bg,list_bg,stop_bg
smevals run evals/grim-debug ; smevals run evals/grim-solve
```

- Gate: Arm B pass rate ≥ Arm A − 5 points → go. Else revisit D1 before the pivot.
- Compare with `evals/harness_health.py`: fail %, tokens/session, format errors.

## 6. Phase 2 — Harness fixes

| Task | Slice | Change | ~Lines |
|---|---|---|---|
| F3 | seeds | `grep_tree`: no match → exit 0 + `no matches` line (rg exit 2 stays an error) | 40 |
| F8 | verbs | `run` header hints: exit 124 → "long-lived? use run_bg"; nonzero + empty stderr → "print why you failed to stderr" | 60 |
| F7a | adapter | Split budget helpers out of `context.py` → `adapter/budget.py` (behavior-preserving) | 150 |
| F7b | adapter | `GRIM_COMPACT_MAX_TOKENS` (default 120_000) caps the budget: `min(window share, cap)` | 60 |
| F6a | verbs | `find` drops `scratch_*` unless the query starts with `scratch` | 50 |
| F6b | adapter | strong-match + recent-library skip `scratch_*`; tool description says when to use it | 40 |
| F6c | seeds | `gardener` proposes archiving `scratch_*` idle > 7 days | 50 |

## 7. Phase 3 — Python-only pivot

| Task | Slice | Change | ~Lines |
|---|---|---|---|
| P0 | seeds | Split `bodies.py` (505) into per-seed modules; `SEEDS` unchanged (behavior-preserving) | 250 |
| P1 | seeds | New `run_cmd`: `subprocess.run(argv, shell=False)`; stderr hint when argv[0] ∈ {cat, head, tail, sed, grep, rg, ls, find} → names the seed (fix 5), or is a library script → "use run(name=…)" (fix 4) | 120 |
| P2 | seeds | New `run_py`: runs stdin as `__main__`; prints "promote to a named script if reused" on success | 80 |
| P3 | seeds | `run_bg`/`list_bg`/`stop_bg`: argv-only `Popen` + pidfile; no `bash -c`, no `exec -a`, no `pgrep` | 150 |
| P4 | seeds | `RETIRED_SEEDS = ("shell",)`: loader archives these on sync; `stats` counts `run_cmd`+`run_py` as the escape rate | 60 |
| P5 | adapter | `tools.py`: write/update/list enums → `python` only | 30 |
| P6 | exec | `dispatch.py`: writable set → `{python}`; bash + extended runners stay **run-only** for legacy rows | 40 |
| P7 | verbs | `update` on a non-python script without `--lang python` → error: "port it: update --lang python" (closes the bash-update loophole); `write` lint rejects non-stdlib imports without a PEP 723 header (24 `ModuleNotFoundError`s) | 100 |
| P8 | adapter | `grimoire.yaml`: python-only prose; `run_cmd`/`run_py` as escape hatches; compose via `subprocess.run(["grim","run",…])`; env check in python; `scratch_` rule; PEP 723 deps; fallback roster updated | 120 |
| P9 | docs | `languages.md`: bash = run-only legacy; evals `run-grim` defaults → `python` | 40 |

**Human steps after P4:**
- `sqlite3 ~/.grimoire/grimoire.db "UPDATE script SET archived=1 WHERE name='run_sh'"`
- `grim init` (re-syncs seeds; archives `shell`).

**Legacy bash follow-through (separate tasks, later):**
- Porting session: agent ports the ~25 bash scripts used in ≥ 3 sessions via
  `update --lang python`, human-reviewed.
- +30 days: gardener proposes archiving unported bash scripts.
- Then: delete the bash runner (strangler's last step) — its own task.

## 8. Risks

- `run_py` becomes the new `shell`. Watch its share in `harness_health.py`;
  gardener can promote frequent snippets to named scripts.
- Python-only may slow ops-heavy tasks (ssh/docker/kamal). Phase 1's gate and
  the ops sessions in `harness_health.py` are the check.
- `uv run` start-up per call: today python averages 3.7 s vs bash 6.3 s per
  successful run, so no regression expected.

## 9. Order and size

L1 → L2 → L3 → L4 → L5 → L6 → L7 → **A/B** → F3 → F8 → F7a → F7b → F6a–c →
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9.
24 commits, each ≤ 300 lines and one slice.

Refs: [subprocess, shell=False](https://docs.python.org/3/library/subprocess.html#security-considerations) ·
[sys.stdlib_module_names](https://docs.python.org/3/library/sys.html#sys.stdlib_module_names) ·
[ast](https://docs.python.org/3/library/ast.html) ·
[PEP 723 inline script metadata](https://peps.python.org/pep-0723/) ·
[uv run --no-project](https://docs.astral.sh/uv/reference/cli/#uv-run) ·
[atexit](https://docs.python.org/3/library/atexit.html)
