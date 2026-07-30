# Grimoire — Build Plan

**A script-hoarding agent harness on mini-swe-agent. CLI: `grim`.**

Grimoire replaces mini-swe-agent's raw-bash action space with six verbs over a SQLite spine: `write`, `update`, `read`, `list`, `find`, `run`. Every action the agent takes becomes a named, versioned artifact with recorded I/O. The agent's memory *is* its executable library — each accumulated script is a new verb in its vocabulary, and `find` is how it remembers it knows the word. Humans and the agent share the same binary and the same database.

The thesis being tested: bash is great, the entire corpus of programming languages is better — and a persistent, searchable corpus of the agent's own scripts compounds, saving tokens on repeat work and leaving the human a usable library behind.

> Status: Phases 0–2 and 2b are done (see git history: `cce0859`..`4bf603c`). Phase 2's pseudocode below is superseded by what actually shipped — mini-swe-agent's real API (2.4.6) diverged from the assumptions this doc was written against; see `src/grim/adapter/CLAUDE.md` and the commit history for the accurate contract. Phases 3–8 below are the remaining work, in order.

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
| D6 | Action grammar | One fenced code block = one `grim` command; script bodies pass via heredoc | Keeps mini's "model emits a code block" contract untouched. |
| D7 | Control-plane enforcement | The mini adapter parses the action and calls grim **in-process** — no shell in the control plane | Hard enforcement of the six-verb constraint; bash exists only *inside* scripts. |
| D8 | Execution model | Stateless by default (script = f(argv, stdin)); per-run timeout; truncated envelope back to context, full output in DB | Matches mini's `subprocess.run` philosophy; executions stay comparable and cacheable. |
| D9 | Executor dispatch | `python → uv run` (PEP 723 inline deps), `bash → bash`, `js/ts → bun`, everything else → `run --json` (Esubaalew/run) | Self-contained Python scripts; `run`'s `--json` envelope maps 1:1 onto the execution row. |
| D10 | Scope model | `global` \| `repo:<fingerprint>`. Repo-scoped by default when cwd is a git repo; promotion to global is human-gated | Contains blast radius of injected/stale scripts; makes the TUI the review queue. |
| D11 | Meta-tooling | Stats, gardener, export ship as *library scripts*, not CLI verbs | The six agent-facing verbs stay closed forever; capability grows in the data plane. |
| D12 | Agent vs human surface | Agent sees exactly six verbs. Humans additionally get `grim init`, `grim doctor`, `grim draft` (Phase 7); the adapter rejects these from the model | Honors the "only these tools may be invoked" constraint without crippling human ops. |
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
│ seed library                │  sh, read_file, apply_patch… + stats/gardener/export
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
    verbs/                  # write.py update.py read.py list.py find.py run.py [Phase 1]
    exec/
      dispatch.py           # language → runner table                          [Phase 1/4]
      envelope.py           # truncation + formatting of observations           [Phase 1/4]
    adapter/
      environment.py        # GrimEnvironment for mini-swe-agent                [Phase 2]
      grimoire.yaml          # mini config: system_template with the protocol ladder [Phase 2]
    seeds/                  # seed script bodies + loader                      [Phase 3]
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

- `write` validates the slug, requires a description, syntax-lints where cheap (`python -m py_compile`, `bash -n`, `bun build --no-bundle` / `node --check`), records `body_hash`, and — crucially — runs a similarity check first. If FTS scores an existing script above threshold, the response leads with: `similar: extract_failing_tests (0.91) — consider 'grim update' or '--parent'`. The anti-duplication nudge lands at the exact moment of temptation.
- `run` stamps `(session_id, seq)` from `GRIM_SESSION` (set by the adapter; defaults to `human-adhoc` for people), stores the full envelope, and returns a truncated observation:

  ```
  [grim] exec #4812 · extract_failing_tests@3 · exit 0 · 1.4s
  --- stdout: first 40 + last 10 of 212 lines · full: grim read --exec 4812 ---
  ...
  ```

- Exit codes: for humans, `grim run` propagates the script's exit code so it composes in shell logic. The adapter reads it from the envelope regardless.

---

## 5. Phases

Estimates assume one developer, ideal days. Phases 0–4 (~2 working weeks) yield a usable daily driver; everything after is compounding on the compounder.

### Phase 0 — Scaffold (0.5–1d) — **done**

`uv init`, package skeleton, `grim init` applies schema + PRAGMAs idempotently, migration runner, pytest smoke test against a temp DB. Landed as: schema v1 migration, `db.py` (`resolve_db_path`/`connect`/`migrate`/`init_db`), `cli.py` (`build_parser`/`cmd_init`/`main`), the `grim` console-script entry point, and smoke tests (`test_db.py`, `test_cli.py`) — 7 passing. **Done when:** fresh init and re-init both succeed; CI runs the smoke suite. *(CI itself is still deferred — see Open questions.)*

### Phase 1 — The six verbs (3–5d)

Implement all verbs per §4 against the schema in §3, including FTS5 search with weighted columns (name > description > body, BM25), write-time lint + similarity nudge, versioned updates, execution capture with truncation, and `--timeout` (kill after N seconds, record exit 124). Start with the `bash` and `python` runners only; the dispatch table is designed for Phase 4 to extend. **Done when:** a scripted end-to-end exercise (write → find → read → run → update → run@old-version) passes on a fresh DB, and invalid Python is rejected at write time with the compiler diagnostic in the response.

### Phase 2 — mini-swe-agent adapter (2–3d)

Subclass the environment; mini's agent loop, linear history, cost limits, and trajectory saving are inherited untouched. The adapter is the hard enforcement point:

```python
class GrimEnvironment(LocalEnvironment):
    def execute(self, action: str) -> dict:
        cmd = parse_grim(action)          # shlex on line 1 + heredoc extraction
        if cmd is None or cmd.verb not in SIX_VERBS:
            return {"output": REMINDER}   # canned protocol reminder, no execution
        return {"output": grim.dispatch(cmd, session=self.session_id)}
```

No shell runs in the control plane — the code block is parsed and dispatched in-process, so the model *cannot* leak back to raw bash. Ship `adapter/grimoire.yaml` with a `system_template` encoding the protocol ladder (§6) and mini's unchanged completion convention. Trajectories gain `exec #id` cross-references into the DB. **Done when:** `mini -c grimoire.yaml` solves a toy task end-to-end using only grim verbs, and an injected `ls -la` action produces the reminder observation instead of output.

### Phase 2b — Streaming model display (optional, ~0.5d)

Cosmetic only — does **not** change the turn-based interaction model. mini's `Model.query()` needs the complete LM response before anything downstream can run: cost calculation needs full usage stats, and action parsing (regex for the textbased model, `tool_calls` for the toolcall model) needs the complete text/payload, not a partial chunk. So the agent still can't decide or act mid-generation, and `InteractiveAgent`'s confirm/reject/redirect loop (Phase 2's research notes) still only happens *between* turns — streaming just removes the blank-pause wait while a turn renders.

Implementation: a custom `Model` subclass (e.g. `grim.adapter.streaming_model.GrimStreamingTextbasedModel(LitellmTextbasedModel)`) overriding `_query` to call `litellm.completion(..., stream=True)`, print each chunk live via `rich.console` as it arrives, and reconstruct a normal response object with litellm's `stream_chunk_builder(chunks)` once the stream ends — so cost calc and action parsing downstream are untouched. Selected the same config-driven way as `GrimEnvironment`, via `model.model_class:` in `grimoire.yaml` — a dotted import path, no mini-swe-agent code changes.

**Fully reversible, no lock-in:** swapping `model.model_class` back to the stock `litellm_textbased` (`LitellmTextbasedModel`) is a one-line yaml edit at any time; the custom class is additive, never a fork of the original.

**Done when:** a live `mini -c grimoire.yaml -m <model>` session renders tokens incrementally instead of pausing until the full response lands, and a test proves the streaming model produces output equivalent to the parent `LitellmTextbasedModel` given the same replayed chunks (existing Phase 2 tests — offline, `DeterministicModel`-based — continue to pass unchanged, since they exercise `GrimEnvironment`, not the model layer).

### Phase 3 — Seed library + protocol tuning (2–3d)

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

Full dispatch table: `python → uv run` (PEP 723 inline metadata makes dependency-bearing scripts self-contained and portable), `js/ts → bun`, fallback → `run --json` for the remaining ~20 languages, with `grim init --check` invoking `run doctor` to report available toolchains. Record `env_fingerprint` per execution. Concurrency: WAL + busy_timeout is sufficient for one agent + one human; a single-writer queue is a later problem. **Done when:** the same Go and Ruby scripts execute via the fallback with correct envelopes, and a Python script declaring a PEP 723 dependency runs cold on a clean machine.

### Phase 5 — Human surfaces (0.5–1d, then 3–5d)

**5a (immediate):** Datasette `metadata.json` with canned queries — recent executions, top scripts by use, failure feed, the affinity view; a README fzf one-liner (`grim list | fzf --preview 'grim read {1}'`); export via the `export_library` seed. **5b (TUI):** Textual app with list/search, preview, run-with-args, execution history, lineage tree — and the **promotion review queue**: repo-scoped scripts become global only on human approval here, with provenance (origin session, task, first run) displayed. The TUI is both benefit #2 and the security gate. **Done when:** a script written by the agent in a repo can be found, reviewed, promoted, and re-run by a human without touching sqlite directly.

### Phase 6 — Retrieval v2 (3–5d)

`find` is the product; this phase is gated on measurement, not vibes. Harvest a labeled query set from real Phase 3–5 sessions (query → which script *should* have matched), measure FTS precision@5, and only then add `sqlite-vec` embeddings in the same DB file with hybrid ranking: BM25 ∪ kNN, reranked by a usage prior (recency decay × success rate from `script_health`). **Done when:** precision@5 measurably improves on the harvested set versus FTS alone.

### Phase 7 — Draft bench (optional, 3–5d)

`grim draft NAME` opens an interactive session — Esubaalew `run`'s persistent per-language REPL or a Jupyter kernel via `jupyter_client` — for iterating on a script with live state; `:freeze` writes the accumulated buffer as a new version. `run_script` itself stays stateless (D8): iterate in the REPL, publish to the DB.

### Phase 8 — Evaluation (4–7d + dogfood weeks)

Two tracks. **Benchmark:** SWE-bench Verified, instances grouped per repo and ordered chronologically, one shared DB per repo; baseline is vanilla mini with the identical model. The compounding thesis makes a falsifiable prediction: *tokens-per-instance and steps-per-instance decline with instance index under Grimoire; the baseline stays flat.* Also report solve rate, reuse rate, escape rate, and ablations (cold vs warm DB; find disabled; `shell` seed removed). Cross-instance state violates standard SWE-bench isolation, so report this as its own track, not a leaderboard number. **Daily driver:** a 2–4 week dogfood diary — library growth, reuse trend, escape-rate trend, and the qualitative question that actually matters: do you reach for the library unprompted?

---

## 6. The prompt protocol (system template core)

The ladder the agent must walk, encoded in `grimoire.yaml`:

```
Before writing any code: grim find "<what you need>".
1. Strong hit → grim read it, then grim run it.
2. Near miss  → fork: grim write --parent <name> with your adaptation.
3. No hit    → grim write a new script. Name it verb_noun. The description
   is a search index entry, not documentation — write it for your future
   self's queries.
Prefer named scripts over `shell` for anything you might do twice.
Before finishing: if you wrote throwaway logic twice this session,
consolidate it into one named script.
```

Naming and description quality are load-bearing (they *are* the retrieval surface), so the write-time similarity nudge and slug lint from Phase 1 back this up mechanically rather than by exhortation alone.

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

---

## 9. Open questions

Deliberately deferred, with a current lean noted. Default target is daily-driver-first (D13) — flip if publishing the benchmark result matters more, since Phase 8's curriculum design would then shape Phases 3–4. Team-shared grimoires (sync via Litestream vs a one-writer server) are out of scope for v1 but the schema's provenance columns anticipate them. Composite synthesis — mining `script_affinity` for recurring chains and auto-proposing a pipeline script — is the most exciting v2 feature and needs nothing but a gardener upgrade. Cross-machine portability is solved for Python by PEP 723; other languages inherit whatever `run doctor` reports, which is acceptable for now.

CI (`.github/workflows/**`, a frozen path) was deferred by explicit human choice at `/setup` time — no git remote existed yet. Generate it once one does; see `.claude/setup-state.json`'s `openItems`.
