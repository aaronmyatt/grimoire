# Grimoire — Build Plan

**A script-hoarding agent harness on mini-swe-agent. CLI: `grim`.**

Grimoire replaces mini-swe-agent's raw-bash action space with six verbs over a SQLite spine: `write`, `update`, `read`, `list`, `find`, `run`. Every action the agent takes becomes a named, versioned artifact with recorded I/O. The agent's memory *is* its executable library — each accumulated script is a new verb in its vocabulary, and `find` is how it remembers it knows the word. Humans and the agent share the same binary and the same database.

The thesis being tested: bash is great, the entire corpus of programming languages is better — and a persistent, searchable corpus of the agent's own scripts compounds, saving tokens on repeat work and leaving the human a usable library behind.

> Status: Phases 0–3 and 2b are done, plus a post-Phase-3 protocol refinement round (language scope, composition, optional `find`, seed list). Since then: adapter hardening (argparse `SystemExit` no longer kills the agent), `grim run` now shows full output by default with opt-in `--head`/`--tail` limits, the composition recursion cap landed (§9 → `GRIM_CALL_DEPTH`), **Phase 5a** (Datasette canned queries + fzf/export docs) shipped, and the adapter migrated from the text-based fenced-block grammar to **native tool-calling** (D6 revised — `GRIM_TOOLS` + a deterministic `submit` stop; the text-based path, including `run.sh --stream`, was removed). **Phase 4 is deliberately deferred** — validating with `python`/`bash` only before extending the runner table. Remaining, in order: Phase 4 (when un-deferred), Phase 5b (TUI), 6, 7, 8.

---

## 1. Decision log

Locked for v1; all revisitable, but changing them mid-build should require a written reason.

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Name / CLI | Grimoire / `grim` | A book of accumulated spells. |
| D2 | Core language | Python ≥3.12, stdlib-only core (`sqlite3`, `argparse`) | Matches mini-swe-agent; zero-dep core in the mini spirit; extensions (Textual, sqlite-vec) stay optional. |
| D3 | Storage | One SQLite file (`~/.grimoire/grimoire.db`, `GRIM_DB` override), WAL mode, FTS5 | Single portable artifact; Datasette-browsable for free; scripts can introspect it. |
| D4 | Versioning | Append-only. `update` writes a new version row; executions pin to an exact version | Reproducibility; forks point at versions, not scripts. |
| D5 | Relationship graphs | Derived views over the execution log — never imperatively maintained edges | Nothing to keep consistent; richer queries (affinity, pipelines) fall out of joins. |
| D6 | Action grammar | **Revised → native tool-calling.** Originally one fenced ` ```grim ` block per turn (heredoc bodies), with completion signalled by a sentinel scanned from command output. Superseded because parsing the model's response text and scanning output for the sentinel proved flaky (a `grim read` of a script whose body held the sentinel falsely finished the task). Now the agent acts via native LLM tool calls over `GRIM_TOOLS` (the six verbs + a terminal `submit`), validated deterministically; the text-based path was ripped out. | Structured actions and a deterministic stop instead of regex/sentinel string-matching. |
| D7 | Control-plane enforcement | The mini adapter parses the action and calls grim **in-process** — no shell in the control plane | Hard enforcement of the six-verb constraint; bash exists only *inside* scripts. |
| D8 | Execution model | Stateless by default (script = f(argv, stdin)); per-run timeout; full output in DB and, since the full-output change, in the observation too — `--head`/`--tail` opt into a truncated envelope | Matches mini's `subprocess.run` philosophy; executions stay comparable and cacheable. |
| D9 | Executor dispatch | `python → uv run` (PEP 723 inline deps), `bash → bash`, `js/ts → bun`, everything else → `run --json` (Esubaalew/run) | Self-contained Python scripts; `run`'s `--json` envelope maps 1:1 onto the execution row. |
| D10 | Scope model | `global` \| `repo:<fingerprint>`. Repo-scoped by default when cwd is a git repo; promotion to global is human-gated | Contains blast radius of injected/stale scripts; makes the TUI the review queue. |
| D11 | Meta-tooling | Stats, gardener, export ship as *library scripts*, not CLI verbs | The six agent-facing verbs stay closed forever; capability grows in the data plane. |
| D12 | Agent vs human surface | Agent sees exactly six verbs. Humans additionally get `grim init`, `grim doctor`, `grim draft` (Phase 7); the adapter rejects these from the model | Honors the "only these tools may be invoked" constraint without crippling human ops. (D6-revised: the agent also gets a terminal `submit` control tool — not a data verb, so the six-verb *library* surface is unchanged.) |
| D13 | First target | Personal daily driver first; benchmark track (Phase 8) second | Compounding shows up where problems actually recur. Flip this if the research result is the priority. |

---

## 2. Architecture

Five components, one database.

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
│ seed library                │  shell, read_file, apply_patch… + stats/gardener/export
├─────────────────────────────┤
│ human surfaces              │  same CLI · Datasette · fzf one-liner · TUI (review queue)
└─────────────────────────────┘
```

Repo layout (current + planned):

```
grimoire/
  pyproject.toml            # uv-managed; core has zero runtime deps
  src/grim/
    cli.py                  # argparse dispatch; exit-code semantics            [done]
    db.py                   # DDL, migrations, query helpers                    [done]
    migrations/              # numbered SQL files (0001_initial.sql landed)      [done]
    verbs/                  # write.py update.py read.py list.py find.py run.py [done]
    exec/
      dispatch.py           # language → runner table, bash+python only        [done; Phase 4 extends]
      envelope.py           # truncation + formatting of observations           [done]
    adapter/
      tools.py              # GRIM_TOOLS schemas + tool_call_to_argv mapper     [done]
      toolcall_model.py     # GrimToolcallModel: grim tool set + action parsing [done]
      environment.py        # GrimEnvironment: tool-call dispatch + submit stop [done]
      grimoire.yaml          # mini config: tool-calling model + system_template [done]
    seeds/                  # bodies.py (9 seeds) + loader.py                  [done]
  surfaces/
    datasette/metadata.json # canned queries                                   [Phase 5]
    tui/                    # Phase 5b (Textual)
  evals/swebench/           # Phase 8 harness + analysis notebooks
  docs/
    build-plan.md           # this file
```

---

## 3. Schema v1

Landed in `src/grim/migrations/0001_initial.sql`, applied by `src/grim/db.py`'s `migrate()`.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys  = ON;
PRAGMA busy_timeout  = 5000;

CREATE TABLE session (
  id          TEXT PRIMARY KEY,          -- uuid or 'human-adhoc'
  kind        TEXT NOT NULL,             -- 'agent' | 'human'
  task        TEXT,                      -- task text for agent sessions
  model       TEXT,
  repo_fingerprint TEXT,                 -- git remote+root hash, if in a repo
  started_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE script (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,      -- slug: ^[a-z][a-z0-9_]{2,63}$ — this is the API
  language    TEXT NOT NULL,
  description TEXT NOT NULL,             -- mandatory; the retrieval surface
  scope       TEXT NOT NULL DEFAULT 'global',
  parent_version_id INTEGER REFERENCES script_version(id),  -- fork lineage
  origin_session_id TEXT REFERENCES session(id),            -- provenance
  seeded      INTEGER NOT NULL DEFAULT 0,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE script_version (
  id          INTEGER PRIMARY KEY,
  script_id   INTEGER NOT NULL REFERENCES script(id),
  version     INTEGER NOT NULL,
  body        TEXT NOT NULL,
  body_hash   TEXT NOT NULL,             -- sha256; cheap exact-dup detection
  changelog   TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  UNIQUE (script_id, version)
);

CREATE TABLE execution (                 -- the append-only event log
  id          INTEGER PRIMARY KEY,
  script_version_id INTEGER NOT NULL REFERENCES script_version(id),
  session_id  TEXT NOT NULL REFERENCES session(id),
  seq         INTEGER NOT NULL,          -- position within session
  argv        TEXT,                      -- JSON array
  stdin       TEXT,
  cwd         TEXT,
  exit_code   INTEGER,
  stdout      TEXT,
  stderr      TEXT,
  duration_ms INTEGER,
  env_fingerprint TEXT,                  -- interpreter versions, for staleness triage
  started_at  TEXT DEFAULT (datetime('now')),
  UNIQUE (session_id, seq)
);

CREATE VIRTUAL TABLE script_fts USING fts5(name, description, body);
-- kept in sync by triggers on script / script_version (latest version wins)

-- "called before/after" is emergent, never bookkept:
CREATE VIEW script_affinity AS
SELECT va.script_id AS a, vb.script_id AS b, COUNT(*) AS times_adjacent
FROM execution ea
JOIN execution eb  ON eb.session_id = ea.session_id AND eb.seq = ea.seq + 1
JOIN script_version va ON va.id = ea.script_version_id
JOIN script_version vb ON vb.id = eb.script_version_id
GROUP BY 1, 2;

CREATE VIEW script_health AS               -- feeds find/list ranking + gardener
SELECT s.id, s.name, COUNT(e.id) AS runs,
       AVG(e.exit_code = 0) AS success_rate,
       MAX(e.started_at)    AS last_used
FROM script s
LEFT JOIN script_version v ON v.script_id = s.id
LEFT JOIN execution e      ON e.script_version_id = v.id
GROUP BY s.id;
```

A `schema_migrations` table tracks applied migrations from day one; `db.migrate()` applies any `migrations/*.sql` not yet recorded, in filename order, idempotently.

---

## 4. CLI surface

Agent-visible verbs (the closed set):

```bash
grim write  --name NAME --lang LANG --desc "..." [--parent NAME[@V]] [--scope S] <<'EOF'
<body>
EOF

grim update NAME --changelog "why" <<'EOF'
<full new body>
EOF

grim read   NAME[@V]                # body + metadata + last 3 execution summaries
grim read   --exec ID [--page N]    # page through a stored execution's full output

grim list   [--scope S] [--lang L] [--limit N] [--offset N]   # terse rows only
grim find   "query" [--limit 5]     # ranked: name · desc · lang · runs · success · last used
grim run    NAME[@V] [--timeout 120] [--stdin-file F] [-- ARGS...]
```

Human-only extras: `grim init [--check]` (create DB, verify FTS5 availability, `run doctor` the toolchains — `init` itself landed in Phase 0; `--check` is Phase 4), `grim doctor`, and later `grim draft` (Phase 7). The adapter rejects these if the model emits them.

Semantics that matter:

- `write` validates the slug, requires a description, rejects any `--lang` outside `exec/dispatch.py`'s `SUPPORTED_LANGUAGES` (currently just `python`/`bash` — Phase 4 extends the runner table, and `SUPPORTED_LANGUAGES` is derived from it, so this check widens for free with no separate edit), syntax-lints where cheap (`python -m py_compile`, `bash -n` — the `bun build --no-bundle` / `node --check` lints land with Phase 4's `js/ts` runner, not yet wired), records `body_hash`, and — crucially — runs a similarity check first. If FTS scores an existing script above threshold, the response leads with: `similar: extract_failing_tests (0.91) — consider 'grim update' or '--parent'`. The anti-duplication nudge lands at the exact moment of temptation.
- `run` stamps `(session_id, seq)` from `GRIM_SESSION` (set by the adapter; defaults to `human-adhoc` for people), stores the full output on the execution row, and by default returns it in full. `--head N`/`--tail M` opt into a first-N/last-M envelope for the occasional huge-output script (the full text is still stored and pageable via `grim read --exec ID`):

  ```
  [grim] exec #4812 · extract_failing_tests@3 · exit 0 · 1.4s
  --- stdout: 212 lines ---          # or, with --head 40 --tail 10:
  --- stdout: first 40 + last 10 of 212 lines ---
  ...
  ```

  Composition is bounded: `run` reads `GRIM_CALL_DEPTH` and rejects past `MAX_CALL_DEPTH` (=8), exposing `depth+1` to the dispatched subprocess so nested `grim run` chains can't recurse without limit (§9).

- Exit codes: for humans, `grim run` propagates the script's exit code so it composes in shell logic. The adapter reads it from the envelope regardless.

---

## 5. Phases

Estimates assume one developer, ideal days. Phases 0–4 (~2 working weeks) yield a usable daily driver; everything after is compounding on the compounder.

### Phase 0 — Scaffold (0.5–1d) — **done**

`uv init`, package skeleton, `grim init` applies schema + PRAGMAs idempotently, migration runner, pytest smoke test against a temp DB. Landed as: schema v1 migration, `db.py` (`resolve_db_path`/`connect`/`migrate`/`init_db`), `cli.py` (`build_parser`/`cmd_init`/`main`), the `grim` console-script entry point, and smoke tests (`test_db.py`, `test_cli.py`) — 7 passing. **Done when:** fresh init and re-init both succeed; CI runs the smoke suite. *(CI itself is still deferred — see Open questions.)*

### Phase 1 — The six verbs (3–5d) — **done**

Implement all verbs per §4 against the schema in §3, including FTS5 search with weighted columns (name > description > body, BM25), write-time lint + similarity nudge, versioned updates, execution capture with truncation, and `--timeout` (kill after N seconds, record exit 124). Start with the `bash` and `python` runners only; the dispatch table is designed for Phase 4 to extend. **Done when:** a scripted end-to-end exercise (write → find → read → run → update → run@old-version) passes on a fresh DB, and invalid Python is rejected at write time with the compiler diagnostic in the response.

### Phase 2 — mini-swe-agent adapter (2–3d) — **done**

Subclass the environment; mini's agent loop, linear history, cost limits, and trajectory saving are inherited untouched. The adapter is the hard enforcement point. What actually shipped (mini-swe-agent 2.4.6's real API differs from an earlier draft of this section — `execute` takes the already-extracted action dict, not the raw model text, and dispatch reuses `cli.main()` in-process rather than a separate `grim.dispatch`; see `src/grim/adapter/CLAUDE.md`):

```python
class GrimEnvironment(LocalEnvironment):
    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        cmd = parse_grim(action["command"])  # shlex on line 1 + heredoc extraction
        if cmd is None:
            return {"output": PROTOCOL_REMINDER, "returncode": 1}  # no execution
        text, exit_code = _invoke(cmd.argv, cmd.stdin, self.session_id)  # cli.main() in-process
        return {"output": text, "returncode": exit_code}
```

No shell runs in the control plane — the code block is parsed and dispatched in-process, so the model *cannot* leak back to raw bash. Ships `adapter/grimoire.yaml` with a `system_template` encoding the protocol ladder (§6) via mini's text-based (non-tool-calling) model — `model.model_class: litellm_textbased` with a `` ```grim ``` `` fence in place of mini's default `` ```mswea_bash_command ``` `` one. Submission reuses the same sentinel convention as vanilla mini (`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`), but `_check_finished` is overridden to match it as any line of `run`'s enveloped output rather than requiring it to be the literal first line — the agent writes a tiny script that echoes the sentinel, then runs it. **Done when:** `mini -c grimoire.yaml` solves a toy task end-to-end using only grim verbs, and an injected `ls -la` action produces the reminder observation instead of output — proved offline with mini's `DeterministicModel` (no live API key needed) in `adapter/test_grimoire_e2e.py`.

### Phase 2b — Streaming model display (optional, ~0.5d) — **done**

Cosmetic only — does **not** change the turn-based interaction model. mini's `Model.query()` needs the complete LM response before anything downstream can run: cost calculation needs full usage stats, and action parsing (regex for the textbased model, `tool_calls` for the toolcall model) needs the complete text/payload, not a partial chunk. So the agent still can't decide or act mid-generation, and `InteractiveAgent`'s confirm/reject/redirect loop (Phase 2's research notes) still only happens *between* turns — streaming just removes the blank-pause wait while a turn renders.

Implementation: a custom `Model` subclass (e.g. `grim.adapter.streaming_model.GrimStreamingTextbasedModel(LitellmTextbasedModel)`) overriding `_query` to call `litellm.completion(..., stream=True)`, print each chunk live via `rich.console` as it arrives, and reconstruct a normal response object with litellm's `stream_chunk_builder(chunks)` once the stream ends — so cost calc and action parsing downstream are untouched. Selected the same config-driven way as `GrimEnvironment`, via `model.model_class:` in `grimoire.yaml` — a dotted import path, no mini-swe-agent code changes.

**Fully reversible, no lock-in:** swapping `model.model_class` back to the stock `litellm_textbased` (`LitellmTextbasedModel`) is a one-line yaml edit at any time; the custom class is additive, never a fork of the original.

**Done when:** a live `mini -c grimoire.yaml -m <model>` session renders tokens incrementally instead of pausing until the full response lands, and a test proves the streaming model produces output equivalent to the parent `LitellmTextbasedModel` given the same replayed chunks (existing Phase 2 tests — offline, `DeterministicModel`-based — continue to pass unchanged, since they exercise `GrimEnvironment`, not the model layer).

### Phase 3 — Seed library + protocol tuning (2–3d) — **done**

Seed on `grim init` (all `seeded=1`, `scope=global`). Named `shell`, not
the originally-planned `sh` — the slug validation `_shared.py` already
enforces (`^[a-z][a-z0-9_]{2,63}$`, minimum 3 chars) rejects 2-char
names, and reusing `write_script` for the seed insert means seeds are
held to that same bar rather than special-cased around it:

| Seed | Purpose |
|------|---------|
| `shell` | escape hatch: run one shell command passed as argv |
| `read_file` / `write_file` | file I/O with line-range support |
| `apply_patch` | unified-diff application via `git apply` with fallback |
| `grep_tree` | ripgrep wrapper with sane defaults |
| `list_dir` | structured directory listing |
| `stats` / `gardener` / `export_library` | meta-tools *as scripts* (D11): usage report; dup/stale sweep proposing archives; dump library to a git-friendly tree |

Bash is thereby demoted to just another script in the corpus on day one. Then tune the economics: instrument shell-escape rate and reuse rate (§7), and truncate `shell` output more aggressively than named-script output so the observed token cost visibly favors promotion to a named script. **Done when:** a 10-task smoke suite runs entirely through the harness and the metrics land in the DB per session.

### Phase 4 — Executor hardening (2–4d)

Full dispatch table: `python → uv run` (PEP 723 inline metadata makes dependency-bearing scripts self-contained and portable), `js/ts → bun`, fallback → `run --json` for the remaining ~20 languages, with `grim init --check` invoking `run doctor` to report available toolchains. `env_fingerprint` recording itself already landed in Phase 1a for the two existing runners (every `execution` row has it); this phase is about extending the *runner table*, not adding the column. Implementation note: `exec/dispatch.py` exports `SUPPORTED_LANGUAGES = frozenset(_RUNNERS)`, and `verbs/write.py`'s `--lang` gate (§4) reads that constant directly — so adding an entry to `_RUNNERS` is sufficient; no follow-up edit to `write.py` is needed to unlock a new language for `write`. Concurrency: WAL + busy_timeout is sufficient for one agent + one human; a single-writer queue is a later problem. **Done when:** the same Go and Ruby scripts execute via the fallback with correct envelopes, and a Python script declaring a PEP 723 dependency runs cold on a clean machine.

### Phase 5 — Human surfaces (0.5–1d, then 3–5d)

**5a (immediate) — done:** Datasette `metadata.json` with canned queries — recent executions, top scripts by use, failure feed, the affinity view (shipped in `surfaces/datasette/metadata.json`); a README fzf one-liner (`grim list | fzf --preview 'grim read {1}'`); export via the `export_library` seed. All three documented under the README's "Human surfaces" section. **5b (TUI):** Textual app with list/search, preview, run-with-args, execution history, lineage tree — and the **promotion review queue**: repo-scoped scripts become global only on human approval here, with provenance (origin session, task, first run) displayed. The TUI is both benefit #2 and the security gate. **Done when:** a script written by the agent in a repo can be found, reviewed, promoted, and re-run by a human without touching sqlite directly.

### Phase 6 — Retrieval v2 (3–5d)

`find` is the product; this phase is gated on measurement, not vibes. Harvest a labeled query set from real Phase 3–5 sessions (query → which script *should* have matched), measure FTS precision@5, and only then add `sqlite-vec` embeddings in the same DB file with hybrid ranking: BM25 ∪ kNN, reranked by a usage prior (recency decay × success rate from `script_health`). **Done when:** precision@5 measurably improves on the harvested set versus FTS alone.

### Phase 7 — Draft bench (optional, 3–5d)

`grim draft NAME` opens an interactive session — Esubaalew `run`'s persistent per-language REPL or a Jupyter kernel via `jupyter_client` — for iterating on a script with live state; `:freeze` writes the accumulated buffer as a new version. `run_script` itself stays stateless (D8): iterate in the REPL, publish to the DB.

### Phase 8 — Evaluation (4–7d + dogfood weeks)

Two tracks. **Benchmark:** SWE-bench Verified, instances grouped per repo and ordered chronologically, one shared DB per repo; baseline is vanilla mini with the identical model. The compounding thesis makes a falsifiable prediction: *tokens-per-instance and steps-per-instance decline with instance index under Grimoire; the baseline stays flat.* Also report solve rate, reuse rate, escape rate, and ablations (cold vs warm DB; find disabled entirely; `shell` seed removed; mandatory- vs optional-`find` prompting — the post-Phase-3 protocol switched `find` from a required first step to a conditional one (§6), which trades find-hit-rate/dup-pressure against saved turns and is itself worth measuring, not just asserting). Cross-instance state violates standard SWE-bench isolation, so report this as its own track, not a leaderboard number. **Daily driver:** a 2–4 week dogfood diary — library growth, reuse trend, escape-rate trend, and the qualitative question that actually matters: do you reach for the library unprompted?

---

## 6. The prompt protocol (system template core)

Superseded once already (Phase 2's original draft assumed a mandatory,
unconditional `find` step) and revised again in a post-Phase-3 tuning
round after live dogfooding. The real ladder, encoded in
`adapter/grimoire.yaml`'s `system_template`:

- **Language scope, stated up front:** scripts may only be written
  `--lang python` or `--lang bash` for now (matches `exec/dispatch.py`'s
  `_RUNNERS`, and is enforced at write time — §4). Other tools/languages
  stay reachable *through* those two — a bash script can invoke anything
  on `PATH`; a python script can `subprocess.run(...)` any CLI tool.
- **Seed list, named:** the system prompt lists all 9 seeded scripts
  (`shell`, `read_file`, `write_file`, `apply_patch`, `grep_tree`,
  `list_dir`, `stats`, `gardener`, `export_library`) with their real
  descriptions from `seeds/bodies.py`, so the agent knows the starter
  library's shape without spending a turn on `grim find`/`grim list`
  first. Flagged as a live test, not a final design — see §9's note on a
  possible human-flaggable "preferred scripts" mechanism.
- **`find` is conditional, not mandatory:** only search first when not
  already sure a suitable script exists — from the seed list above, from
  having just written/run it this session, or because the task is
  clearly novel. Skipping `find` when the agent already knows what it
  needs avoids a wasted turn. On a hit: strong match → `grim read` it,
  then `grim run` it (`grim read` also previews the script's last 3 runs
  — exit code, duration, stdout snippet — so past results can be reused
  via `grim read --exec ID [--page N]` instead of regenerated); near
  miss → fork with `grim write --parent <name>`; no hit → `grim write` a
  new script, named `verb_noun`, with a description written as a search
  index entry for future queries, not documentation.
- **Composition, not duplication:** a script can call another by
  shelling out to `grim run OTHER_NAME` (bash: `grim run other_name --
  args`; python: `subprocess.run(["grim", "run", "other_name", ...],
  capture_output=True, text=True)`), reusing its logic instead of
  re-deriving it. A nested call's captured output always leads with a
  `[grim] exec #id ...` header and a `--- stdout: N lines ---` envelope
  — the prompt tells the agent to treat it as opaque text (check
  `returncode`, log/forward stdout) rather than something to parse line
  by line. This depended on a concurrency fix in `verbs/run.py`
  (`ensure_session`'s write transaction was previously held open across
  the entire blocking dispatch call, so a nested `grim run` would
  deadlock on the SQLite write lock) — composition wasn't actually
  usable in the agent's hands until that landed.
- **Fix-on-error:** after every `grim run`, check `<returncode>` in the
  observation. Nonzero means the script has a bug — `grim update` it and
  rerun before moving on to anything else. Never leave a broken script
  behind.
- **Prefer named scripts** over throwaway one-offs for anything the
  agent might do twice.
- **Submission:** call the `submit` tool with the final answer as
  `result` (D6-revised). This is the deterministic stop — there is no
  output sentinel and no "finish" script to write. (Originally: write a
  tiny script echoing `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` and run it —
  replaced because scanning output for that string false-triggered on any
  script that merely contained it.)

Naming and description quality are load-bearing (they *are* the
retrieval surface), so the write-time similarity nudge and slug lint
from Phase 1 back this up mechanically rather than by exhortation alone.

---

## 7. Metrics

All computable from the DB; `grim run stats` prints them (D11).

| Metric | Definition | Watching for |
|--------|-----------|--------------|
| Reuse rate | runs of scripts authored in *earlier* sessions ÷ all runs | the compounding signal |
| Shell-escape rate | runs of `shell` ÷ all runs | the bypass failure mode |
| Find hit rate / p@5 | finds followed by a run of a result; labeled precision | retrieval quality (gates Phase 6) |
| Dup pressure | writes that triggered the similarity nudge ÷ all writes | library rot leading indicator |
| Tokens & steps per task | from mini's cost tracking, joined to sessions | the headline thesis curve |
| Active library | scripts run in last 30d ÷ total unarchived | gardener effectiveness |

Phase 3's `stats` seed implements reuse rate, shell-escape rate, and
active library from the schema as-is. Dup pressure is **not yet
computable** — `write_script`'s similarity nudge (build plan §4) is
computed and printed at write time only, never persisted, so there's no
data source for "writes that triggered it" today. Fixing this means
either a `script.similarity_nudged` column or a lightweight event log,
either way touching `verbs/write.py` — deliberately deferred rather
than reopening that already-shipped file as a side effect of Phase 3.
Find hit rate stays gated on Phase 6 as originally scoped.

---

## 8. Risks

| Risk | Mitigation |
|------|------------|
| Library rot / near-duplicates | Write-time similarity nudge; `body_hash` exact-dup check; `gardener` sweeps proposing archives; `archived` flag keeps history intact. |
| Model bypasses the library via `shell` | Protocol ladder; asymmetric output truncation; escape-rate metric with a review threshold. |
| Stale scripts silently rot | `script_health` success-rate surfaced in `find`/`list`; `env_fingerprint` for triage; gardener re-verification runs. |
| Persistent prompt-injection payloads | Persistence changes the threat model: an injected script can be re-run later with unearned trust. Same sandbox as vanilla mini (Docker env for untrusted repos); repo scope by default (D10); human-gated promotion in the TUI; provenance always displayed. |
| Naming drift kills retrieval | Slug lint + mandatory descriptions at write; gardener proposes renames; Phase 6 embeddings reduce dependence on exact wording. |
| FTS alone underperforms | Phase 6 is measurement-gated; hybrid ranking only ships if it beats BM25 on the harvested query set. |
| Context pollution from listings | `list` is paginated and ruthlessly terse; full bodies only via explicit `read`. |
| Optional `find` misses/duplicates existing scripts | `find` is no longer a mandatory first step (§6) — mitigated today only by the hardcoded seed list in the prompt and the write-time similarity nudge; no dup-pressure metric exists yet to detect regression (§7), and this tradeoff is explicitly a live test, not a settled design. |
| Composition enables unbounded/recursive script chains | A dispatched script can shell out to `grim run` on another script (§6), including transitively. **Mitigated:** `run_script` bounds the chain via `GRIM_CALL_DEPTH` — rejected past `MAX_CALL_DEPTH` (=8), incremented per nested call through the inherited env (§9). No true cycle *detection* (a short A↔B loop still runs until it hits the depth cap), but the chain can no longer recurse without limit. |

---

## 9. Open questions

Deliberately deferred, with a current lean noted. Default target is daily-driver-first (D13) — flip if publishing the benchmark result matters more, since Phase 8's curriculum design would then shape Phases 3–4. Team-shared grimoires (sync via Litestream vs a one-writer server) are out of scope for v1 but the schema's provenance columns anticipate them. Composite synthesis — mining `script_affinity` for recurring chains and auto-proposing a pipeline script — is the most exciting v2 feature and needs nothing but a gardener upgrade. Cross-machine portability is solved for Python by PEP 723; other languages inherit whatever `run doctor` reports, which is acceptable for now.

CI (`.github/workflows/**`, a frozen path) was deferred by explicit human choice at `/setup` time — no git remote existed yet. Generate it once one does; see `.claude/setup-state.json`'s `openItems`.

`grimoire.yaml`'s `system_template` now hardcodes the 9 seeded scripts by name and description (a deliberate live test, not a final design — see its own commit). Being weighed as a follow-up: human-flaggable "preferred" scripts — closer to a general "skills" mechanism (a script or set of scripts a human pins for the agent to always know about) than a fixed seed list. Would need a schema change (something like `script.pinned`) and a prompt-assembly step that queries pinned scripts instead of a static string. Not built — captured here so the idea isn't lost.

Composition (§6, §8 Risks) recursion-depth cap — **done.** `run_script`
now reads `GRIM_CALL_DEPTH`, rejects past `MAX_CALL_DEPTH` (=8) with a
`CallDepthExceeded` error, and exposes `depth+1` to the dispatched
subprocess so every nested `grim run` inherits a higher count (the same
`os.environ`-inheritance path `GRIM_SESSION` uses); the env is restored
after dispatch so the in-process adapter doesn't accumulate depth across
turns. Still open: there is no *cycle detection* — a short A↔B loop runs
until it trips the depth cap rather than being caught as a cycle — and the
cap is a fixed constant, not configurable. Both are acceptable for now.
