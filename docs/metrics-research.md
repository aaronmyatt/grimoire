# Grimoire metrics — research

Researched 2026-10-13. Baselines read from the live `~/.grimoire/grimoire.db`
and `~/.grimoire/instrumentation.jsonl` (2026-08-05 → 2026-10-09).

> **Status — 2026-10-14.** Shipped as the `metrics` slice:
> `grim stats [summary|weekly|sessions|runs|scripts] [--format table|json|jsonl|csv|tsv|md] [--since YYYY-MM-DD]`.
> The weekly scorecard at the bottom is `grim stats weekly --format md`. Gap
> **#2 (session outcome) is closed**; gap **#3 (inflated reuse) is fixed in
> `metrics/queries.py`** but *not* in the `stats` seed or the five
> `evals/*/run-metrics` checkers. Gaps #1, #4, #5 stay open. The baselines
> below predate the slice.

## TL;DR

- **Time to completion** — use *active time*, not wall time.
  - Active time = session `wall_ms` minus `agent.human_wait` ms.
  - Wall time is junk here: one session ran 55 h, 99.9 % of it waiting on you.
  - Today: p50 **4 min**, p90 **30 min** (80 sessions).
- **Hit rate** — track it at two levels.
  - *Find hit rate*: a `find` that leads to a read/run of a script it returned.
  - *Library hit rate*: a session that ran at least one script made in an
    earlier session. Today: **68 %** of sessions.
- **Bug found**: the shipped `reuse_rate` counts seed runs as reuse.
  - Seeds have `origin_session_id = 'human-adhoc'`, not NULL.
  - So every `shell` run counts as "reuse". Reported ~85–100 %; true is ~7–45 %.
- **Qualitative** — a weekly 10-minute review: 3 transcripts, 5 new scripts,
  one trust score, one "did I reach for it unprompted?" answer.
- **Growth signal** — failed `find` queries and heavy-`shell` tasks are the
  backlog. They say what the library is missing.

## What exists today

| Metric | Where | State |
|---|---|---|
| Reuse rate | `grim stats` (metrics slice) · `stats` seed · `run-metrics` | `grim stats` **correct**; seed + evals inflated (gap #3) |
| Shell-escape rate | `grim stats`, `stats` seed | Works |
| Active library % | `stats` seed; `grim stats scripts` (`last_run_at`) | Works |
| Steps, cost, format errors | eval `run-metrics` (trajectory) | Works, evals only |
| Weekly scorecard | `grim stats weekly --format md` | Works, minus the rows below |
| Per-phase timing, active time, turns, tokens | `adapter/trace.py` → `instrumentation.jsonl`; `grim stats sessions`/`weekly` | Works (gap #2 closed) |
| Script success rate | `script_health` view | Works |
| Find hit rate / p@5 | build plan §7 | **Not computable** — results not logged |
| Dup pressure | build plan §7 | **Not computable** — nudge not stored |

## Baseline (weekly, live DB)

"True reuse" = runs of non-seed scripts made in an earlier session.

| Week | Runs | Shell % | Seed % | True reuse % | Exit-0 % | Sessions |
|---|---|---|---|---|---|---|
| W31 | 4425 | 61 | 73 | 11 | 87 | 142 |
| W32 | 4526 | 50 | 67 | 19 | 89 | 68 |
| W33 | 1174 | 36 | 48 | 44 | 82 | 9 |
| W34 | 526 | 73 | 86 | 7 | 82 | 11 |
| W35 | 86 | 77 | 87 | 7 | 90 | 3 |
| W36 | 168 | 76 | 82 | 7 | 89 | 3 |
| W40 | 13 | 23 | 23 | 77 | 85 | 3 |

- Other numbers:
  - 593 agent-written scripts. 152 (26 %) were ever run again in a later session.
  - 2.15 versions per script on average.
  - Your own `grim run` calls: 34 (W31), 32 (W32), 2 (W34), then 0.
  - After a `find`, the next tool call was: read 228, run 218, list 29, write 6.

## 1. Time to completion

- **Active time per task** — the headline number.
  - `wall_ms − Σ agent.human_wait.duration_ms`, per session close event.
  - Report p50 and p90. Means get wrecked by a few long tasks.
- **Turns per task** and **tokens per task** — same story, less noise.
  - Turns = `llm.completion` count. Tokens = sum of `total_tokens` on those spans.
- **Split by outcome.** A fast give-up is not a win.
  - Needs the session close event to carry `exit_status` (gap #2).
- **Split time by phase.** Shows where to optimize.
  - `llm.completion` vs `tool.run` vs everything else. Already in `totals`.
- **The thesis curve** — does time drop as the library grows?
  - Field data mixes easy and hard tasks, so the trend lies.
  - Use the evals for this: same tasks, warm DB vs cold DB, steps vs task index.
  - The prediction is in build plan §8: grim slopes down, vanilla mini stays flat.
  - Agent skill-library papers report the same kind of gain: ASI cut steps
    10.7–15.3 %; AWM cut steps on WebArena.

## 2. Hit rate

Four levels, cheapest first.

- **Library hit rate (per session).** Did any earlier-session script get run?
  - Computable today. 68 % now. Watch the trend, not the level.
- **Find hit rate (per query).** `find` → read/run of a *returned* script within
  the next 3 tool calls, with no `write` in between.
  - Needs the find span to log the names it returned (gap #1).
  - Today's proxy (next call is read/run) is ~92 %, but run may be `shell`.
- **Zero-result rate** and **reformulation rate.** From site-search practice.
  - Zero-result = finds that returned nothing.
  - Reformulation = a `find` right after another `find`. High means the first
    list was bad.
- **Labeled precision@5 and MRR.** The Phase 6 gate.
  - Precision@5 = share of the top 5 results that are right.
  - MRR (mean reciprocal rank) = average of 1 / rank of the first right result.
    1.0 means the right script is always first.
  - Build the labeled set from real `find` queries once gap #1 lands.
  - Voyager reported 96.5 % top-5 retrieval accuracy on its skill library — a
    useful ceiling to aim at.

## 3. Reuse and library health

- **True reuse rate.** Fix the seed bug first (gap #3).
- **Script retention.** Share of new scripts run again in a later session
  within 7 / 30 days. Today 26 % ever.
  - This is HEART's "retention" applied to scripts, not users.
- **Time to first reuse.** Days from `write` to first run in another session.
- **Seed share** and **shell-escape rate.** Seeds at 80 %+ means the agent
  leans on primitives, not on its own library.
- **First-run success.** Exit 0 on the first run of a new version.
  - Plus **fix loops**: versions written before the first green run.
- **Dup pressure.** Writes that tripped the similarity nudge (gap #4).

## 4. Reliability (evals only)

- **Pass rate per suite**, per model, warm vs cold.
- **pass^k** — chance that *all* k runs of a task pass.
  - pass@k asks "did it ever work". pass^k asks "can I trust it". τ-bench
    introduced it. For a daily driver, pass^k is the one that matters.
- **Format-error rate** — already in `run-metrics`.

## 5. Qualitative metrics

Built from HEART (Google) and SPACE (Forsgren et al.). Both say: no single
number; mix behavior with how it feels.

- **Weekly 10-minute review.** Write answers in a diary file.
  1. *Did I reach for the library unprompted this week?* Yes/no + one example.
     - Behavior check: count of your own `human-adhoc` runs. It fell to 0
       after W34. That is the most important signal in this doc.
  2. *Trust (1–5):* would I run a script the agent wrote without reading it?
  3. *Read 3 transcripts.* Tag each with one failure mode:
     `shell-bypass` · `missed-find` · `dup-write` · `wrong-script` ·
     `fix-loop` · `none`. Anthropic's eval guide says read transcripts — the
     numbers lie without them.
  4. *Rate 5 new scripts* on a 4-point rubric (1 point each):
     clear `verb_noun` name · description works as a search entry ·
     takes args instead of hard-coded values · correct.
  5. *One "saved me" and one "got in my way" moment.*
- **Growth backlog** (what to build next):
  - Top zero-result `find` queries → missing scripts or bad descriptions.
  - Tasks with the most `shell` runs → which commands to promote to scripts.
  - Top `script_affinity` pairs → candidate pipeline scripts.

## Gaps to close

Each is a small, separate task. None is done here.

| # | Gap | Fix | Slice |
|---|---|---|---|
| 1 | Find results not logged | Add returned script names to the `tool.find` span | `adapter` |
| 2 | ~~Session close has no outcome~~ **closed** | `exit_status`, turns (`llm_calls`), and tokens now ride the close event; `grim stats sessions` reads them | `adapter` ✅ |
| 3 | Reuse counts seeds | **Fixed in `metrics/queries.py`** (`s.seeded = 0`); still open in the `stats` seed and `evals/*/run-metrics` | `metrics` ✅ / `seeds` + `evals` |
| 4 | Dup nudge not stored | Persist "nudge fired" per write | `verbs` |
| 5 | No task type | Optional tag on the session (e.g. `debug`, `ops`, `research`) | `adapter` |

## Weekly scorecard (proposed)

| Number | Source | Want |
|---|---|---|
| Active time p50 / p90 | instrumentation | ↓ |
| Turns per task p50 | instrumentation | ↓ |
| Library hit rate | DB | ↑ |
| True reuse rate | DB (after gap #3) | ↑ |
| Find hit rate | instrumentation (after gap #1) | ↑ |
| Shell-escape rate | DB | ↓ |
| Script retention (30 d) | DB | ↑ |
| Human runs / week | DB | ↑ |
| Trust score (1–5) | diary | ↑ |

## Pasteable queries

True reuse rate by week (fixes the seed bug):

```sql
-- Run: sqlite3 "file:$HOME/.grimoire/grimoire.db?mode=ro" < this.sql
-- mode=ro opens read-only. Ref: https://www.sqlite.org/uri.html
SELECT strftime('%Y-W%W', e.started_at) AS week,
       ROUND(AVG(s.seeded = 0 AND s.origin_session_id != e.session_id) * 100, 1)
         AS true_reuse_pct,
       ROUND(AVG(s.name = 'shell') * 100, 1) AS shell_pct
FROM execution e
JOIN script_version sv ON sv.id = e.script_version_id
JOIN script s ON s.id = sv.script_id
GROUP BY week ORDER BY week;
```

Library hit rate (share of sessions that ran an earlier session's script):

```sql
SELECT ROUND(AVG(hit) * 100, 1) AS library_hit_pct
FROM (SELECT e.session_id,
             MAX(s.seeded = 0 AND s.origin_session_id != e.session_id) AS hit
      FROM execution e
      JOIN script_version sv ON sv.id = e.script_version_id
      JOIN script s ON s.id = sv.script_id
      WHERE e.session_id != 'human-adhoc'
      GROUP BY e.session_id);
```

## Sources

- [τ-bench (pass^k)](https://arxiv.org/pdf/2406.12045)
- [Anthropic — Demystifying evals for AI agents](https://anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Voyager — skill library and retrieval accuracy](https://arxiv.org/pdf/2305.16291)
- [Agent Workflow Memory](https://arxiv.org/html/2409.07429v1)
- [Agent Skill Induction](https://www.arxiv.org/abs/2504.06821)
- [SkillFlow — skill reuse metrics](https://arxiv.org/pdf/2604.17308)
- [Google HEART framework (CHI 2010)](https://research.google.com/pubs/archive/36299.pdf)
- [SPACE of developer productivity (ACM Queue)](https://queue.acm.org/detail.cfm?id=3454124)
- [Retrieval metrics: precision@k, MRR](https://weaviate.io/blog/retrieval-evaluation-metrics)
- [Site search: zero-result and reformulation rate](https://www.xictron.com/en/blog/site-search-analytics-onsite-search-2026/)
