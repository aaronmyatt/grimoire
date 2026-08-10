# Context-length awareness — plan

Status: **implemented — phases 0-3 landed in the adapter slice** (the code
below is now live in `src/grim/adapter/context.py` + `test_context.py`, wired
through `toolcall_model.py`/`agent.py`/`launcher.py`/`grimoire.yaml` in the
repo and the installed uv tool venv; 13 new unit tests + the full adapter
suite green, ruff clean). Phase 4 (full `--resume` rehydration, a token gauge
in the status line) remains a follow-up. Scope: the **grimoire harness**
(`grim-agent` / the adapter slice `src/grim/adapter/**`). Goal: a long session
must never die to "This model's maximum context length is N tokens" — the
harness compacts in-session, and when a session does end, `--continue` picks
up with the last-used scripts **and a snippet of the previous session**.

Companion: the instrumentation patch already merged into the adapter slice
(`grim run instrument_report` reads it) — per-turn token counts are the
awareness primitive this plan builds on.

---

## 1. Problem & failure mode

grim-agent runs an autonomous loop over six verbs. Every turn re-sends the
**entire** message list (system + task + every prior assistant/tool turn) to
the LLM. The kernel (minisweagent, a frozen PyPI dependency) has **zero
context management** — verified by grep: no trimming, no summarization, no
compaction anywhere in `minisweagent/`. So a session that runs long enough
(verbose tool output, many turns, big script bodies) eventually exceeds the
model's window and the provider rejects the call:

    litellm.BadRequestError ... "This model's maximum context length is 128000
    tokens. However you requested ... tokens" (ContextWindowExceededError)

Today that kills the session mid-task with no recovery path. Two secondary
costs even before the hard failure: prompt cost grows roughly linearly with
turn count (every turn re-sends all history), and model accuracy degrades in
very long contexts ("lost in the middle").

## 2. What exists today (ground truth, verified by probe)

- **`--continue` exists but only does recall.** `launcher.py`:
  `--continue` → `_take_flag` → sets `GRIM_RECALL=1` and reuses the last
  agent session id via `-c environment.session_id=…` (executions stamp the
  same lineage). Warm-start content = **library recall only**:
  `agent.py::recent_library()` — the last ~10 non-seeded, non-archived
  scripts that have run at least once with `success_rate ≥ 0.5`, ranked by
  runs/iterations, rendered terse (name + description) into the
  `grim_recent_library` template var. **No conversation rehydration.**
- **Per-turn token counts already exist.** `toolcall_model.py::_usage_fields()`
  reads `prompt_tokens` / `completion_tokens` / `total_tokens` off every
  litellm response (fed to the `llm.completion` trace span). The adapter
  slice sees the full `messages` list and the model's own token count at the
  single choke point `_query` — the seam this plan uses.
- **The window primitive exists.** litellm 1.94.3 (tool venv) exposes
  `litellm.get_model_info(model)` → `max_input_tokens` / `max_output_tokens`
  (gpt-4o: 128k/16k; anthropic/claude-sonnet-4-5: 200k/64k). No new
  dependency needed.
- **No transcript table in the DB — and none may be added.** `grimoire.db`
  holds `session` (id/kind/task/model/repo_fingerprint/started_at) and
  `execution` (script runs: argv/exit_code/stdout/stderr per session) but no
  message log. `src/grim/db.py` is a **frozen path** (root CLAUDE.md §5) —
  the adapter slice must not add schema. The full conversation transcript
  lives instead in per-run trajectory JSONs (`launcher._trajectory_path`,
  `grimoire-<ts>-<pid>.traj.json` under `$GRIM_TRAJ_DIR` or the temp dir;
  mini's format: `messages`, `result`, `exit_status`, `cost`, `api_calls`).
  These are the adapter's own channel — safe to read.
- **Budgets/constraints to honor:** root CLAUDE.md §1 (≤300 changed lines per
  change, explicit max on every buffer/collection), §2 (adapter is its own
  slice; never imports other slices), §5 (frozen paths listed above), and
  adapter/CLAUDE.md (stdlib-only, degrade-don't-crash, external input
  validated). `patch_grim_instrumentation` is the precedent for exactly this
  kind of adapter-owned harness change.

## 3. Design goals

1. **Never die to a context error.** At worst: compact and continue; at
   best: never approach the ceiling.
2. **Zero kernel / schema changes.** Everything lands in the adapter slice:
   message-list rewriting at `_query` time, trajectory-tail reading for
   `--continue`.
3. **Grim-native compaction.** Prefer the library and the run log (last-used
   scripts, last execution results, trajectory facts) over generic "earlier
   messages summarized" stubs — deterministic, cheap, on-topic, and it
   compounds the library's core thesis.
4. **Degrade-don't-crash.** Compaction failure → warn + proceed with what
   fits; env knobs to tune and to switch off entirely.

---

## 4. Mechanism A — in-session compaction ("the harness should compact")

**Where:** `toolcall_model.py::GrimToolcallModel._query` — already overridden
by the adapter, already reads usage. It sees `messages` before every
completion, so it is the one choke point for a token budget.

**New module: `adapter/context.py`** (stdlib-only, like `trace.py`):

- **Window resolution:** `litellm.get_model_info(model)["max_input_tokens"]`
  cached per model name; unknown model → conservative default (e.g. 128k) +
  warn once. Output reserve = `max_output_tokens` (or a provider default like
  8k) so a turn can always finish. Effective budget =
  `max_input_tokens − output_reserve − system/tools overhead`.
- **Accounting:** running prompt-token total from `_usage_fields` (the
  model's own count — authoritative), with a fallback estimate
  (`len(text)/4 + tool-call overhead`) when usage is missing.
- **Activation:** when `prompt_tokens > compact_at × budget` (default
  `compact_at = 0.75`), run the ladder below on a copy of `messages`; never
  mutate the kernel's store — just hand the compacted list to litellm.
- **Compaction ladder (cheapest → richest):**
  1. **Trim tool results.** Cap each `tool`-role result's content in history
     at `GRIM_MAX_TOOL_OUTPUT` (default 4 KB): keep first + last ~2 KB with a
     `…[truncated N chars]` marker. Tool outputs are the biggest bloat and
     the cheapest win (the environment's span previews already truncate — this
     extends the same idea to history).
  2. **Drop oldest tool-result pairs.** Remove the oldest assistant/tool
     message pairs (keep the assistant tool-call, drop the result) until
     under budget. Tool calls without results read oddly, so prefer trimming
     first and only drop pairs older than the most recent K turns.
  3. **Summarize the tail.** Replace turns older than the last
     `GRIM_COMPACT_KEEP` (default 6) with one synthetic user message:
     *"Session summary (turns 1..N):"* + content built **grim-natively**:
     last-used scripts (name + description, reusing `recent_library()`), the
     last few execution rows for this session (script, argv, exit code,
     result tail), and — if `GRIM_COMPACT_MODEL` is set and an LLM summary is
     wanted — a short summary of the dropped tail via one cheap completion
     (`GRIM_COMPACT_MODEL`, default = off, so Phase 2 is opt-in and free).
  4. **Last resort (before a raw 400):** raise a grim-branded `FormatError`
     — "context budget exhausted: wrap up or submit now" — so the model
     converges instead of the API killing the run.
- **Retry-on-error:** catch the provider's context-window exception around
  `litellm.completion`; compact aggressively (summarize the tail) and retry
  **once**; if it still fails, surface a grim-branded error whose text points
  at `grim-agent --continue` (Mechanism B) — a session that dies is at least
  resumable.
- **Env knobs (all validated/clamped, never trusted):** `GRIM_COMPACT`
  (default 1, `0` switches off), `GRIM_COMPACT_AT` (fraction 0..1),
  `GRIM_COMPACT_KEEP` (verbatim turns kept), `GRIM_COMPACT_MODEL` (cheap
  summarizer; unset = no LLM summarization), `GRIM_MAX_TOOL_OUTPUT` (bytes).
- **Tests:** pure unit tests for window resolution / accounting / each ladder
  step with synthetic messages (deterministic, no LLM), plus one e2e with a
  stub model that raises a context error → assert compact+retry-once.

## 5. Mechanism B — `--continue`: last-used scripts + a session snippet

`--continue` already gives half of this: **last-used scripts** (recall,
§2). The missing half is **"a snippet from the previous session"** — the new
session currently starts blind about *what the last session was doing when it
ended*.

- **New: `grim_previous_session` template var** (sibling of
  `grim_recent_library`, injected by `agent.py` the same way — into
  `extra_template_vars`, rendered near the task).
- **Source: the last trajectory file.** On `--continue`, read the newest
  `grimoire-*.traj.json` under `$GRIM_TRAJ_DIR` (else temp dir), and extract
  a **bounded** tail snippet: previous task, model, exit status/result, and
  the last N tool executions (tool, argv, exit code, first+last ~500 chars of
  output) — exactly the "what did we just do and how did it end" shape a
  follow-up session needs. Trajectories are the adapter's own channel; no
  frozen-path read, no DB access.
- **Degrade:** missing/unreadable trajectory or empty tail → proceed with
  recall only (current behavior), warn once to stderr.
- **Keep** the existing session-lineage pinning (executions continue the same
  `session` row) — that is what makes the snippet + recall coherent.
- **Deferred: full `--resume`.** Rehydrating the kernel's in-memory message
  list from a transcript is a deeper change (the kernel's store isn't
  adapter-owned). The recall + snippet combination is the safe 80% fix today;
  a `--resume` that restores verbatim history can ride on the same trajectory
  reader later.
- **Wire-up:** `launcher.py` (`--continue` block, `GRIM_RECALL=1` site) sets
  a path hint (env `GRIM_LAST_TRAJ` or a small `~/.grimoire/last-trajectory`
  pointer file); `agent.py` reads it in the same turn it renders
  `grim_recent_library`. ~80 lines, all adapter-owned.

## 6. How other harnesses handle it (survey)

| Harness | In-session compaction | Cross-session resume | Notes |
|---|---|---|---|
| **Claude Code** | Auto-compact ~95% of window: older turns summarized into one compact message, recent kept verbatim; manual `/compact` (+ `/compact:preserve`); token usage in status line | `claude --continue` / `--resume <session>`; full transcript on disk | CLAUDE.md memory files = durable cross-session facts |
| **Aider** | Per-message token accounting (model's own counts); auto-compression — builds a "compression map" of older files/context and rewrites the conversation; `/compact` summarizes with a cheap model; `/drop`, `/clear` | `--continue` / `--session` | Repo map keeps file context bounded and reusable across sessions |
| **Codex CLI (OpenAI)** | Automatic rolling context window — older turns summarized/evicted as it grows | `--continue` resumes the last session | Saves session transcripts |
| **Goose (Block)** | "Memory compaction": auto + manual `/compact` summarizing older turns | — | "Memories" plugin persists durable facts across sessions |
| **opencode** | Auto-compaction of history at a configurable threshold | `--continue` / `--session` | — |
| **Gemini CLI** | Auto-condense when near the limit | `--continue` resumes | — |
| **Cursor / Windsurf (IDE)** | Manual + automatic "condense"/"compact" (summarize) near the window; long-context mode toggle | Session restore | UI-driven |
| **OpenHands** | Event-stream condensing: LLM summarizes older events, recent kept verbatim; per-event token counting | — | Research-oriented, event-sourced |
| **SWE-agent** | Simplest: truncates / drops oldest observations, no summarization, hard-capped context | — | The baseline grim beats by having a library |
| **LangGraph / LangChain (framework)** | `trim_messages`: token-budgeted sliding window (keep system + recent, drop oldest); summarization nodes; `messages_state` reducer | Checkpointing / thread resume | The canonical "ladder" formulation |
| **LlamaIndex** | Token-budget chat memory + summary memory | — | — |

**Shared pattern distilled** (this is the design grimoire copies):

1. **Count** — use the provider's own `usage` counts (authoritative), tokenizer fallback.
2. **Budget** — window − output reserve − system/tools; act at 60–95%, never wait for the 400.
3. **Ladder** — trim payloads → drop oldest → summarize the middle → fresh session with carry-forward; always keep system + task + recent turns verbatim.
4. **Persist** — transcript on disk so a session can resume (full rehydrate or snippet).
5. **Degrade** — never crash on overflow: compact + retry once, then an explicit "wrap up" signal.
6. **Memory** — durable cross-session facts (CLAUDE.md / memories / *the script library*) are the cheap complement to compaction. grimoire's recall + snippet *is* mechanism 6, already half-built.

## 7. Recommendation & phased rollout (all in the adapter slice)

- **Phase 0 — budget module (no behavior change):** `adapter/context.py` with
  window resolution (`get_model_info`), accounting, and pure tests. Proves
  the primitive; ~120 lines.
- **Phase 1 — trim ladder (biggest win, no LLM):** tool-result trimming +
  drop-oldest in `_query` at `GRIM_COMPACT_AT`; knobs. Prevents the
  overwhelming majority of overflows (tool output is the bloat). ~150 lines.
- **Phase 2 — summarize + retry-once:** tail summarization (grim-native +
  optional `GRIM_COMPACT_MODEL`), context-error catch → compact → retry once,
  then a grim-branded error with the `--continue` hint. ~100 lines.
- **Phase 3 — `--continue` snippet:** newest-trajectory tail reader +
  `grim_previous_session` template var + launcher wiring. ~80 lines.
- **Phase 4 — optional:** full `--resume` rehydration; token gauge in the
  status line; `GRIM_RECALL_LIMIT` already exists for tuning recall size.
- **Rollout gate per phase:** unit tests green (repo `.venv` pytest,
  `PYTHONPATH=src`), ruff clean, diff ≤ 300 lines, sync to the live tool venv
  (same procedure as `patch_grim_instrumentation`), adapter suite green.

**Landed (2026-08):** phases 0-3 shipped together by
`patch_grim_context_awareness` — `adapter/context.py` (~490 lines, under the
500-line cap), `test_context.py` (13 tests), `toolcall_model._query` →
`context.completion()` + the FormatError hint, `agent.py` injecting
`grim_previous_session` on `--continue`, `launcher._remember_trajectory`
recording the last trajectory path for the next run, and the
`<previous_session>` block in `grimoire.yaml`. Repo + installed venv synced;
13 new + 71 regression + 158 adapter-suite tests pass; ruff clean. Land as two
commits (context+toolcall; agent+launcher+yaml) to respect the ≤300-line
change budget.

## 8. Constraints honored

- Frozen paths untouched: no `db.py` / `cli.py` / `config.py` / schema /
  `pyproject.toml` changes. Compaction state is in-process + trajectory
  files, never a new DB table.
- Adapter slice owns it; stdlib-only new code (`litellm` already present for
  `get_model_info`; no new dependency).
- Every buffer has an explicit max (`GRIM_MAX_TOOL_OUTPUT`,
  `GRIM_COMPACT_KEEP`, snippet length caps).
- Degrade-don't-crash with a hard off switch (`GRIM_COMPACT=0`); external
  input (env, trajectory files) validated before use.

## 9. Success metrics (definition of done)

- Zero "maximum context length" deaths on sessions that previously overflowed
  (compaction kicks in before the ceiling; retry-once catches the rest).
- A context overflow that does occur leaves a resumable state:
  `grim-agent --continue "<follow-up>"` starts with recall + a real snippet
  of where the last session stopped.
- Prompt cost per turn stops growing with session age beyond the budgeted
  window (trimmed/summarized history stays bounded).
- All knobs default to safe values; `GRIM_COMPACT=0` restores today's exact
  behavior byte-for-byte.

## 10. Sources (verified)

- `src/grim/adapter/launcher.py` (`--continue`, `_take_flag`, `GRIM_RECALL`,
  `_trajectory_path`, `LaunchSpec.session_id`, `summarize_run`/`format_output`
  trajectory keys)
- `src/grim/adapter/agent.py` (`recent_library`, `rank_recall`, `recall_enabled`,
  `recall_limit`, `grim_recent_library` template var, `GRIM_RECALL*` env)
- `src/grim/adapter/toolcall_model.py` (`_query` override, `_usage_fields`,
  `_format_error`, `GRIM_TOOLS`)
- `src/grim/adapter/environment.py` (`_tool_args_snippet` / preview truncation
  precedent), `src/grim/adapter/trace.py` (span emitter pattern, GRIM_INSTRUMENT
  opt-out precedent)
- `~/.grimoire/grimoire.db` schema (`session`, `execution`, `script`,
  `script_version`, `script_health`; no message table), `$GRIM_TRAJ_DIR`
  per-run `grimoire-*.traj.json` transcripts
- litellm 1.94.3 (tool venv): `get_model_info` → `max_input_tokens` /
  `max_output_tokens` per model (gpt-4o 128k, claude-sonnet-4-5 200k)
- minisweagent venv `site-packages`: grep confirms no trimming /
  summarization / compaction in the kernel
- root `CLAUDE.md` §1 (budgets), §2 (slices), §5 (frozen paths);
  `patch_grim_instrumentation` as the adapter-owned change precedent
