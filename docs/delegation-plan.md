# Delegation plan — grim as a general-purpose system driver

Status: **plan only, 2026-11-19, at `185baa4`.** Nothing here is built. Sources:
`~/.grimoire/run/roadmap_race/synthesis.md` (S), `~/.grimoire/run/roadmap-codex/result.md` (CX),
`~/.grimoire/run/roadmap-claude/result.md` (CL). Each phase runs as one or more changes. Each
change is ≤300 lines in one slice, under root `CLAUDE.md`. **UNVERIFIED** marks claims nobody has checked.

## 1. Goal and definition of done

The human's goal: *"grim [is] a general purpose system driver that will delegate to two more
expensive models from cheap models and ideally respond promptly to any and all system level and
particularly tmux requests."*

| # | Requirement | Done means (observable, testable) |
|---|---|---|
| R1 | System driving | A cheap model serves a routine system request (jobs, logs, ps, disk, git, launchd, docker, sqlite) in **≤1 tool call**. It uses a seed or `run_cmd`, and never delegates. "What is running?" costs **0 tool calls**, because a jobs line rides in every observation. `grim find` puts the right seed in the top 3 for ≥27/30 labeled queries. |
| R2 | Cheap → two expensive delegates | A hard judgement task is served in **3 tool calls**: `agent_ask both`, `agent_wait`, `agent_compare`. The call returns one packet of ≤4 KiB with both verdicts, agreed and disputed claims, wall time, and cost in USD or `null`. Every outcome is a typed state. "Exit 0 with no answer" can't happen: a delegate that ends without a validated result exits **3**, never 0. |
| R3 | Prompt tmux response | Each tmux move (list/read/send/start/stop) completes in **<100 ms p99**, measured in-process at the adapter tool span. That means ≥300 warm samples per move, with no `sleep` and no poll loop. Each wait is a single blocking call that returns within 200 ms of the signal. |

## 2. Starting state

**Built and tested on fakes** (`src/grim/seeds/`):
- tmux driver (`bodies_tmux.py`, 487/500 lines): `tmux_start/list/read/send/stop/wait`. tmux window tags are the registry, and a `pane-died` hook signals `grim-JOB`.
- Delegate driver (`bodies_agent.py`, 316 lines): `agent_start` (TUI, ready-polling, a dialog table) and `agent_read` (state from screen text).
- Browser driver (`web_*`). The six verbs are unchanged. `adapter/environment.py` imports `grim.cli` in-process, so the cheap model pays no CLI start-up.

**Measured** (S §1.3, CX §5, CL §5):
- `tmux_list` takes 49 ms in-script and 114/124 ms (median/p95) through the CLI.
- Raw `tmux list-windows -F` takes ≈10 ms.
- `tmux_send` takes 455 ms for long text (300 ms of fixed sleep).
- `agent_start` takes 1.0–5.9 s of ready-polling.
- `uv run --no-project python -c pass` takes 28–34 ms.

**Lived library** (CX §2):
- 672 scripts, 13,782 executions. Escape rate 64.4%, cross-session reuse 15%. 242 active scripts ran once.
- `claude_tmux` ran 153 times at 73.9% success. `agent_start` ran 3 times, `agent_read` 7 times, `tmux_send` once.
- `web_*` and `gardener` have 0 runs.

**Known defects driving this plan:**
- Dead pane → `tmux_wait` exits 0 (`bodies_tmux.py:240-243`, the race in S §0).
- File exists → `agent_read` says DONE, even if empty (`bodies_agent.py:278-281`).
- `tmux_stop` fires the job-end signal, and the first waiter consumes the wake (`bodies_tmux.py:442`, CL §2.4).
- Unbounded reads: whole log and full scrollback (`bodies_tmux.py:310,315`). A 2.28 GB log exists.
- argv loses a trailing `;`; a path with a space dies 127 (`bodies_tmux.py:57`).
- Four concurrent same-name starts make four windows (`bodies_tmux.py:71-116`).
- `find` ignores usage (`verbs/find.py:43-44`).
- **Drift hazard:** the live library has `agent_start@3`, which flattens whitespace before matching codex's update dialog (`flat = re.sub(r"\s+", "", low)`). The checkout `bodies_agent.py:183` does not. The next `grim init` re-syncs the seed to the checkout body and **reverts the fix**: `seeds/CLAUDE.md` convergence invariant.

**Aspirational, not built:** every checklist box is `[ ]`. `04-delegates.md` names `agent_send`/`agent_stop`, which do not exist. `@eval` scenarios have never run. Real codex/pi were seen only on start screens. There is no `[delegates.*]` config and no recorded delegate cost.

**Verified 2026-11-19 for this plan** (`--help` on the real binaries):
- claude 2.1.322 has `-p`, `--output-format`, `--max-budget-usd`, `--tools`, `--settings`, `--permission-mode` and `--no-session-persistence`. It has **no `--max-turns`**, so drop it from CX §4.
- codex-cli 0.170.0 `exec` has `--json`, `-o/--output-last-message`, `-s/--sandbox`, `--skip-git-repo-check`, `--ephemeral` and `-m`, and reads the prompt from stdin.
- **UNVERIFIED:** JSON field names (`result`, `total_cost_usd`, `is_error`, the codex `usage` events).

## 3. Decision records

**(a) Delegate transport.**
- **Chosen:** a headless worker for the automated pair: `agent_exec` runs `claude -p --output-format json` or `codex exec --json -o FILE -`, with the brief on stdin.
- **TUI `agent_start` stays** for attended pairing. A human steps in there (surfaces §3.8).
- **Rejected:** TUI-only with turn-end hooks (CL P10).
- **Why:** the TUI's ready-race is exactly what failed in the race. Headless has no dialogs, no scraping, and gives a real exit code and JSON for free.

**(b) Completion.**
- **Chosen:** completion is a stamped machine fact. Only `agent_done JOB` declares DONE, after it validates `result.md`: UTF-8, 200 B–16 KiB, five required headings, ≥1 numbered claim.
- It writes `<run_dir>/JOB/status.json` atomically (the durable outcome), then sets `@grim_done` and signals, in one tmux call.
- **Rejected:** file-exists.
- **Why:** an empty or partial file reads as success today. A stamp checked before any wait also makes a consumed wake harmless.
- `status.json` is an outcome record next to `result.md`, not a job list. tmux stays the only registry of what is live (surfaces §3.2).

**(c) Job identity.**
- **Chosen:** stamps + honest exit codes now. The delegation path mints unique job names (`ID-AGENT`). A non-blocking `flock` name guard comes in `tmux_start` in Phase 5.
- **Rejected:** generation IDs with opaque handles on every seed (CX P1, ≈230 lines touching all six seeds).
- **Why:** the observed failure was false success, not identity. Unique names plus a lock close the measured 4-way race at a quarter of the cost. Generation IDs stay deferred until a failure needs them.

**(d) Waiting on N.**
- **Chosen:** `agent_wait SECONDS JOB…` makes one blocking call for ≤4 jobs.
- It checks durable state first (`status.json`, `@grim_done`, pane dead, window gone). It then blocks on one `tmux wait-for` child per pending job, via `selectors` on their pipes, under one deadline, and re-checks durable state after every wake.
- **Rejected:** CX's cohort worker with per-cohort lease and private channels.
- **Why:** with status checked first, a consumed wake is harmless. `wait-for -S` wakes every current waiter (tmux `cmd-wait-for.c`, UNVERIFIED by test until Phase 3), so no lock is needed.

**(e) Cost control.**
- **Chosen:** enforce only what is real.
  - Wall-clock deadline: `agent_exec` kills the delegate's process group. This is hard.
  - claude `--max-budget-usd`, via config. It is provider-side and may overshoot (UNVERIFIED).
  - codex has no dollar cap. Tokens are recorded and `cost_usd: null`.
  - At most 2 expensive invocations per ask, with no automatic retry.
  - `meta.json` records cost or `null`. Phase 6's `agent_stats` turns it into a scoreboard.
- **Rejected:** a `max_usd_per_ask` key presented as a hard cap (CL §4.2).
- **Why:** a cap nobody enforces is a lie the cheap model will trust (CX §4).

## 4. Phases

Rules for every phase:
- Make each listed change a separate commit on a feature branch.
- Run the slice suite and show its output.
- Keep `bodies_*.py` files under 500 lines.
- Append an `ABSTRACTIONS.md` entry for each new copy of the tmux prelude or job lookup. Never extract one.
- Register each new body module in `seeds/bodies.py`'s `SEEDS`, the same way `AGENT_SEEDS` is (`bodies.py:10,415`).
- Update `seeds/CLAUDE.md` when the public surface changes.
- Tick a checklist box only when a named passing test proves its scenario. Put the test name in the commit message.

### Phase 1 — Honest completion (removes "success that isn't")

**Capability:** a delegate is DONE only after its answer passes validation. A delegate or job that ends without one exits 3. A second wait on a finished delegate returns at once.

1. **1a (≤170 lines, behavior-preserving):** move the `TMUX_READ` constant verbatim from `bodies_tmux.py:258-329` to the new `seeds/bodies_tmux_read.py`. Import it into the `TMUX_SEEDS` tuple at the same position.
   - Proof: the existing suite is green, and `grim init` on a synced test DB reports no drifted seed.
   - Why: it frees ~75 lines. Steps 1e and 5b need the room.
2. **1b (≤90, tests only):** add characterization tests that pin today's behavior.
   - `tmux_wait` on a dead agent exits 0.
   - `agent_read` on an empty `result.md` prints DONE.
3. **1c (≤40):** port the live `agent_start@3` whitespace-flattened dialog match into `bodies_agent.py:183`.
   - Regression test: a fake TUI shows `2.Skip3.Skipuntilnextversion`, and `agent_start` sends Escape.
4. **1d (≤260):** new `seeds/bodies_delegate.py` with `AGENT_DONE`. Edit `AGENT_START` and `AGENT_READ`.
   - argv: `grim run agent_done -- JOB`. It is run by the delegate.
   - On validation failure it exits 1. Each problem goes to stderr. Nothing is stamped or signalled.
   - On pass:
     - It writes `status.json` (`{"job","state":"DONE","bytes","sha256","stamped_at"}`) via a temp file and `os.replace`.
     - It runs `tmux set-option -w -t WIN @grim_done EPOCH:BYTES:SHA8 \; wait-for -S grim-JOB` as one client.
     - It prints `done JOB (N bytes)`.
   - Change `agent_start`'s done contract (`bodies_agent.py:139-142`) to:
     `Write your answer to RESULT with the headings above, then run: grim run agent_done -- JOB (it checks the file; fix what it reports).`
   - Change `agent_read`:
     - Add `#{@grim_done}` to `ROW`.
     - DONE requires the stamp, or `status.json` DONE whose sha256 matches `result.md`. The second case also covers a window already stopped.
     - A file without a stamp: if the pane is live, print `UNCHECKED` and exit 0. If the pane is dead, print `EXITED (exit N) · result UNCHECKED` and exit 3.
     - No result and a dead pane: `EXITED`, exit 3.
   - Tests in `seeds/test_bodies_delegate.py`, using the fake TUI. The fake runs the contract's `grim run agent_done` line. UNVERIFIED that `grim` is on the pane's PATH in tests; if not, the fake calls `sys.executable -m grim.cli`. Cases:
     - (a) A result written without `agent_done` reads as UNCHECKED.
     - (b) An empty file or a missing heading makes `agent_done` exit 1, naming the heading.
     - (c) A valid file is stamped, and `status.json` sha256 equals the file's.
     - (d) The window stopped after DONE still reads as DONE.
5. **1e (≤120):** honest exits in `TMUX_WAIT` (`bodies_tmux.py:178-256`).
   - Add `#{@grim_kind}\t#{@grim_done}` to its `ROW`.
   - Before blocking:
     - Stamped → exit 0 at once.
     - Dead with exit 0 and kind ≠ agent → 0.
     - Dead agent without a stamp → 3, printing `EXITED (exit N): no result`.
     - Dead with a non-zero exit → 3.
   - After a wake, apply the same rule. A job seen before the wait whose window is gone after it → 3, printing `stopped before it finished`.
   - A raw signal not tied to a job is unchanged (0).
   - Tests:
     - (e) The race: the fake exits 0 with no result, and `tmux_wait` exits 3.
     - (f) The signal is consumed by one waiter, and a second `tmux_wait` on a stamped job exits 0 at once.
     - (g) `tmux_stop` during a wait gives the waiter 3.
     - (h) A plain job: exit 0 gives 0, exit 7 gives 3.
6. **Ticks:**
   - Feature 4: "Collect the answer", "The delegate forgot the contract".
   - Feature 2: "A job signals when it ends".
   - §3.5: "The signal came first".

### Phase 2 — Headless delegates

**Capability:** one call starts claude, codex or both, headless, in attachable tmux windows. Each publishes a validated result or a typed failure, plus usage.

1. **2a (≤60):** `tmux_start` gains an optional first flag, `--kind KIND`. KIND is `job` (the default) or `agent`, matched against `^[a-z]{1,16}$` and the allowlist.
   - argv: `tmux_start [--kind agent] JOB PROGRAM [ARG...]`.
   - Why: `agent_ask` reuses the one window-opener instead of adding a fourth copy (ABSTRACTIONS.md, "now three copies").
   - Test: an agent-kind job shows in `tmux_list` as `agent`. An unknown kind exits 2 and starts nothing.
2. **2b (≤280):** new `seeds/bodies_headless.py` with `AGENT_EXEC`.
   - argv: `agent_exec AGENT JOB DIR DEADLINE_EPOCH`. It runs inside the job pane.
   - Read `<run_dir>/JOB/brief.md` and `[delegates.AGENT]` from config: `bin`, plus the new required `exec_args` list. If `exec_args` is missing, exit 2 and say "powers must be stated".
   - Build the argv:
     - claude: `[bin, "-p", "--output-format", "json", *exec_args]`
     - codex: `[bin, "exec", "--json", "-o", TMP, *exec_args, "-"]`
   - Run it with `start_new_session=True` (so the child gets its own process group), the brief on stdin, stdout capped at 1 MiB, and `timeout = DEADLINE_EPOCH - now`. On timeout: `killpg` TERM, wait 2 s, then KILL.
   - Pull the answer and usage defensively. A missing field becomes `null`.
     - claude: `result`, `total_cost_usd`, `is_error`, `usage`.
     - codex: the `-o` file, plus the last JSONL event that carries `usage`.
     - All of these field names are UNVERIFIED.
   - Write `result.md` atomically, then `meta.json` (`agent`, `exit`, `wall_s`, `cost_usd|null`, `input_tokens|null`, `output_tokens|null`, `brief_bytes`).
   - Run `grim run agent_done -- JOB`.
   - Classify into `status.json.state`, in this order: `TIMEOUT`, `AUTH`, `CONTEXT`, `FAILED`, `INVALID_RESULT`, `DONE`.
     - AUTH hints: `login`, `authenticate`, `api key`, `401`.
     - CONTEXT hints: `context length`, `prompt is too long`.
     - `FAILED` is any other non-zero exit. `INVALID_RESULT` is when `agent_done` exits 1.
     - Write the classified state into `status.json` (`agent_done` only ever writes DONE).
   - Exit 0 only for DONE, else 3. The pane-died hook signals.
   - Tests: fake `claude`/`codex` via `GRIM_DELEGATES_CONFIG` `bin=`, one per case:
     - Canned valid JSON → DONE with `cost_usd`.
     - Exit 1 with "please login" → AUTH.
     - Sleeps past the deadline → TIMEOUT, and the child process group is gone.
     - Prose without headings → INVALID_RESULT.
     - A 40-line brief → arrives whole on the fake's stdin.
3. **2c (≤200):** `AGENT_ASK` in `bodies_headless.py`.
   - argv: `agent_ask AGENTS ID DIR [SECONDS]`, brief on stdin. AGENTS is `claude`, `codex` or `both`. SECONDS defaults to 1200, cap 3500. The brief is ≤20,000 chars.
   - Preflight everything before starting anything: the bins resolve, `exec_args` is set, DIR is absolute and exists, and the `ID-AGENT` names are free. Failures exit 127, 2 or 1, and nothing is half-started.
   - Compute `DEADLINE = now + SECONDS` **once**. For each agent:
     - write `brief.md`;
     - run `grim run tmux_start -- --kind agent ID-AGENT grim run --timeout SECONDS+60 agent_exec -- AGENT ID-AGENT DIR DEADLINE`, with cwd=DIR.
   - Print `started ID-claude ID-codex · deadline HH:MM:SS · next: agent_wait SECONDS ID-claude ID-codex`.
   - Tests:
     - `both` → two agent windows, and both fakes see the identical brief.
     - codex missing → exit 127, no window opened.
4. **Manual paid smoke (human approves; see §8):** with a 3-line brief, run
   `grim run agent_ask -- both smoke-1 /tmp/grim-smoke 300 < brief.md`.
   Record the real JSON field names and fix the parsers before Phase 3.
5. **Ticks:**
   - Feature 4: "A long brief arrives whole", "A delegate's powers are stated".

### Phase 3 — One wait, one packet

**Capability:** the cheap model blocks once for both delegates. It then gets a ≤4 KiB mechanical comparison, with no model in the loop.

1. **3a (≤220):** `AGENT_WAIT` in `bodies_delegate.py`.
   - argv: `agent_wait SECONDS JOB [JOB...]`. `MAX_JOBS = 4`, `SECONDS ≤ 3500`.
   - One `list-windows` call plus a `status.json` read per job decides terminal or pending:
     - Terminal: `status.json` present, pane dead, or window gone.
     - Pending: otherwise.
   - For pending jobs, `Popen(tmux wait-for grim-JOB)` each. `selectors.select(timeout=remaining)` on their stdout pipes; EOF means woken. Re-check durable state for each woken job.
   - Re-arm at most 3 times per job. Kill the leftover `wait-for` clients at the deadline.
   - Print one line per job, e.g. `ID-claude DONE 14m02s $0.83 6.1KiB` or `ID-codex AUTH exit 1`.
   - Exit codes: 0 if all DONE; 3 if all terminal with some failure; 124 if any job is still pending.
   - Tests:
     - Two fakes stamp at 0.2 s and 0.4 s → wall time <0.6 s.
     - Already stamped before the call → <100 ms.
     - A wake consumed by another waiter → still 0.
     - Neither signals → 124, and no `wait-for` child remains (`pgrep` count).
     - `time.sleep` is absent from the body (a grep test).
2. **3b (≤240):** new `seeds/bodies_compare.py` with `AGENT_COMPARE`.
   - argv: `agent_compare JOB_A JOB_B`.
   - Parse each DONE `result.md` by heading.
   - Verify each claim's evidence:
     - `path:line` → the file exists under DIR (from `meta.json`/brief) and has ≥ that many lines;
     - `` `command` `` → recorded, never run.
   - Normalize claims: lowercase, strip punctuation, drop a fixed 40-word stopword list. Jaccard ≥0.5 counts as the same claim. Verdicts are `AGREE` when their Jaccard is ≥0.5, else `DIFFER`.
   - Print the packet (§5 shape), hard-capped at 4096 bytes. Each claim is ≤160 chars; at most 8 only-A and 8 only-B claims.
   - Exit codes: 0 if both DONE; 3 if one DONE (single source); 1 if neither.
   - Tests: fixture pairs for agree, differ, one MALFORMED (a heading missing), and one AUTH. Also: the packet is ≤4096 bytes for two 16 KiB results.
3. **Ticks:**
   - Feature 2: "The signal never comes".
   - §3.5: "The signal arrives", "The signal never comes".
   - Feature 8: "Every wait has a cap".

### Phase 4 — Teach the route; make "what's running" free

**Capability:** the cheap model takes tmux and system requests directly, in ≤1 call (0 for "what is running"). It delegates only by rule.

1. **4a (≤80, tests only):** add prompt goldens for the rendered `adapter/grimoire.yaml` (Feature 3's note; root `CLAUDE.md` §4).
2. **4b (≤120):** rewrite `grimoire.yaml:117-124` with the §5 rules and the three-call loop.
   - Keep `agent_start` text as "attended pairing only".
   - Offline replay test in `adapter/test_prompt_jobs.py`: the rendered prompt names `agent_ask`, `agent_wait` and `agent_compare`. It forbids delegating tmux requests.
3. **4c (≤150):** jobs line in observations, in `adapter/environment.py`.
   - After each tool observation, one `tmux list-windows -t =grim -F` call (≈10 ms).
   - If tagged windows exist, append `jobs: NAME KIND STATE AGE · …`. Cap it at 8 jobs and 300 chars.
   - Agent STATE comes from `@grim_done` (DONE) or pane dead.
   - Tests use an injected runner:
     - The line is present with 2 jobs.
     - The line is absent with none.
     - The line is capped with 12 jobs.
     - A tmux error yields no line and no exception.
4. **Ticks:**
   - Feature 3: "The prompt teaches surfaces".
   - §3.2: "Listing asks the surface".

### Phase 5 — tmux fast and bounded

**Capability:** every tmux move is measured, has a bounded cost at any log size, keeps argv exact, and can't double-start a name.

1. **5a (≤200):** new self-contained `evals/tmux-latency/bench.py` plus a counting `tmux` shim placed first on PATH.
   - It drives the same in-process entry the adapter uses (`adapter/environment.py:79`; confirm the call). The private socket is `-L grimbench`.
   - Sample counts: 300 warm runs per move, then 30 each for the cold, 100-window, 50 MB-log, multiline and same-name-parallel cases.
   - It prints p50/p95/p99, failures, and tmux clients per call.
   - Commit the baseline JSON alone (it is a generated artifact).
2. **5b (≤120):** bounded `tmux_read` in `bodies_tmux_read.py`.
   - Dead log: `seek(max(0, size - 4 MiB))`.
   - Live pane: `capture-pane -S -(LINES+SKIP)` instead of `-S -`.
   - The status line gains `truncated` when the bound bit.
   - Test: a 50 MB log reads in <100 ms and returns the right last lines.
3. **5c (≤120):** `tmux_start` name guard.
   - Non-blocking `fcntl.flock` on `<run_dir>/.tmux-start-<socket>.lock`, held from lookup to the last tag.
   - The loser exits 1 with "name is being started".
   - Test: 4 parallel same-name starts → 1 window, 3 exits of 1.
4. **5d (≤125):** argv preservation in `tmux_start`.
   - A sole token that is an existing executable path is never shlex-split.
   - Multi-arg argv is passed through a fixed `python -c` exec stub with base64-JSON argv. Then tmux never re-parses `;`.
   - Tests:
     - `/bin/echo 'literal;'` prints `literal;`.
     - A path containing a space runs.
     - A Unicode argument and a leading `-` survive.
5. **5e (≤120):** new `run_dir_prune [--keep-days N] [--max-mb M] [--yes]` seed.
   - Without `--yes` it lists what it would delete.
   - It deletes only logs and job dirs whose window is gone and older than N days.
6. **5f (conditional, ≤150, `exec` slice):** if 5a shows p99 ≥100 ms after 5b–5d, move the `dispatch._env_fingerprint` version probe off the hot path (CX P8). Otherwise skip it, and say so.
7. **Ticks:**
   - Feature 1: "Read a job's screen", "A dead job keeps its story", "A job name is taken", "No shell in the command".
   - §3.3: "Nothing is held open".

### Phase 6 — Find the right seed; score the delegates

**Capability:** an unknown request reaches the right seed in one `find`. Delegate choice has data behind it.

1. **6a (≤150, tests only):** 30 labeled queries as a fixture DB in `verbs/test_find.py`, pinning today's results.
   - Queries include "what is running", "tmux list", "read job", "stop it", "ask codex", "docker processes", and read vs write.
2. **6b (≤210):** in `verbs/find.py`:
   - Promote an exact slug, or its space-separated form, to top-1.
   - Rank name/description hits above body-only hits.
   - Tie-break on `log1p(runs) * success_rate` among plausible matches only.
   - Add intent words to the `TMUX_SEEDS`/`DELEGATE_SEEDS` descriptions ("what is running", "jobs", "tail", "kill", "ask", "review").
   - Gate: ≥27/30 correct top-3, 100% exact-slug top-1, p95 find ≤50 ms.
   - This is build-plan Phase 6's measurement gate. No embeddings.
3. **6c (≤150):** new `agent_stats [DAYS]` seed, reading `<run_dir>/*/status.json` and `meta.json`.
   - Per agent: asks, DONE rate, median wall time, median `cost_usd` (or `unknown`), and counts per failure state.
   - Test: fixture files → the expected table.
4. **Ticks:** none. `find` has no checklist box; its gate is the 30-query set.

### Phase 7 — Attended path honest; audit the checklist

**Capability:** the TUI path never prints `started` for a brief that did not land. The checklist reflects what is proven.

1. **7a (≤80):** brief-landed check in `AGENT_START`.
   - After Enter, run ≤10 captures 0.5 s apart. This is an attended-path-only bounded poll, a documented exception to "no polls".
   - Require a working mark, the codex OSC `state=working`, or the brief's first 40 chars. Otherwise exit 1 with `brief NOT taken`.
   - Test: a fake TUI whose default dialog eats Enter → exit 1.
2. **7b (docs only):** fix `04-delegates.md`: it uses `tmux_send`/`tmux_stop`, and it gains the `agent_ask` path. Then tick every remaining box whose scenario has a passing test, naming each test.
3. **7c (human):** archive `claude_tmux`/`codex_tmux`. They are `seeded=0`, so only a human may. Do it after 5 successful real `agent_ask` runs. Their JSON registries are the drift `surfaces.md` §3.2 bans.
4. **Ticks:**
   - Feature 4: "First-run dialogs are known".
   - Plus whatever the audit proves.

## 5. The delegation loop, concretely

**Routing rules for the cheap model** (go in `grimoire.yaml`, Phase 4b):
1. **Never delegate** tmux moves, read-only system lookups (ps, disk, launchctl, docker ps, git status, sqlite, tail), or any task where `find` returns a script with runs ≥3 and success ≥0.9.
2. **Delegate to one** with `agent_ask claude|codex` in any of these cases:
   - after ≥12 own steps;
   - after 2 failed runs of the same script;
   - the task spans ≥3 files or asks design/refactor/why.
   - Pick codex for a repo-local diagnosis and claude for broad reading. Writes go to one delegate only, attended (`agent_start`) or with write `exec_args`.
3. **Fan out to both** only for read-only judgement: review, compare, plan, or root cause without a repro.

**The argv sequence** (the adapter forms; the CLI forms are `grim run NAME -- ARGS`):

```text
# 1. Start both on an identical brief; returns in <1 s. ID = ask-<topic>-<n>, caller-chosen.
run(name="agent_ask", args=["both", "ask-race-1", "/abs/repo", "1200"], stdin=BRIEF)
# 2. One blocking call under the shared deadline. Give run a timeout 30 s above the wait.
run(name="agent_wait", args=["1200", "ask-race-1-claude", "ask-race-1-codex"], timeout=1230)
# 3. Mechanical arbitration, no model: a packet of at most 4 KiB.
run(name="agent_compare", args=["ask-race-1-claude", "ask-race-1-codex"])
```

If `GRIM_TIME_BUDGET_S` clamps step 2 to 124, the cheap model submits: "still running: run `agent_wait` then `agent_compare` with these IDs". The delegates are tmux jobs, so they outlive the session. `status.json` keeps their outcome.

**Brief template** (identical for both; the cheap model fills in the `<>` fields):

```text
# Task (grim ask ID)
<one standalone paragraph: the question, in the human's words>

## Scope
- Directory: <ABS_DIR>. Read-only: do not edit files.
- Read first: <at most 6 paths>
- Already observed: <at most 8 facts, each with path:line or a grim execution id>

## Reply with exactly these five headings, under 6 KiB in total
## Verdict
One sentence.
## Answer
At most 300 words.
## Claims
1. <claim> — evidence: <path:line> | `<command>` → <observed output>
## Confidence
high | medium | low — one line why.
## Open
What you could not check. Label every guess UNVERIFIED.

Stop at the deadline even if unfinished; say what is left under ## Open.
```

The headless path sends nothing more. The TUI path (`agent_start`) appends the `agent_done` line from step 1d.

**Packet shape** (`agent_compare` stdout):

```text
ask-race-1  claude DONE 14m02s $0.83 verified 9/11 · codex DONE 11m40s cost=null tok=412k verified 7/7
verdict A: "stamp completion before anything else"  verdict B: "stamp completion first"  → AGREE
agreed (6) · only-A (2): #4 headless removes dialogs … · #9 cap logs … · only-B (1): #3 …
```

**How the cheap model spends tokens** (≤5 tool calls, ≤6k tokens):
- It reads only the packet (≈1k tokens). **AGREE** → it answers from the verdicts and agreed claims.
- **DIFFER** → it prefers the higher `verified k/n`. On a tie it runs **one** known probe (`read_file`, `run_cmd`). Still unsettled → it reports what is open, with both lines side by side.
- It reads at most one full `result.md`, and only on DIFFER.

**Failure taxonomy:**

| Failure | Detected by | Response |
|---|---|---|
| Auth | `agent_exec` exit ≠0 with an auth hint → `AUTH` | No retry. Tell the human the exact step (`claude /login`, `codex login`). Use the other side, marked "single source". |
| Trust/update dialog | Headless: none expected (UNVERIFIED for codex's update check; it would show as `TIMEOUT`). TUI: profile table (1c) and brief-landed check (7a). | TUI exits 1 with `brief NOT taken`. Re-run headless. |
| Never finishes | `agent_exec` deadline → `TIMEOUT`, process group killed. `agent_wait` → 124 if still pending. | Run `agent_read JOB` once for the tail, then `tmux_stop JOB`. Use the other side alone. |
| Empty/garbage result | `agent_done` refuses → `INVALID_RESULT`. On the TUI path the delegate gets the reason while still running. | Never DONE. Single source. No auto re-ask. |
| Consumed wake | Stamp/`status.json` checked before and after every wait (1e, 3a) | Returns at once. No hang. |
| Context blowout | `CONTEXT` hint on non-zero exit, or `is_error` (UNVERIFIED) | No same-brief retry. Re-ask once only if the human approves, with fewer paths. |
| Runaway cost | claude `--max-budget-usd` (provider-side). `meta.json.cost_usd` in the packet. Wall-clock kill. | The packet shows cost. `agent_stats` trends it. No third invocation. |

## 6. Budgets and instrumentation

| Phase | Latency budget (local p99 unless stated) | Cost budget |
|---|---|---|
| 1 | `agent_done` ≤150 ms. A stamped `tmux_wait` returns in ≤100 ms. | 0 |
| 2 | `agent_ask both` ack ≤1 s p95 (two nested `grim run tmux_start`). `agent_exec` overhead outside the model ≤300 ms. | 1 paid smoke per agent, ≤$0.50 total |
| 3 | `agent_wait` returns ≤200 ms after the last stamp; already-done in ≤100 ms. `agent_compare` ≤300 ms on 2×16 KiB. | 0 |
| 4 | Jobs line adds ≤15 ms per observation. | 0 |
| 5 | Each of list/read/send(short)/start/stop **<100 ms in-process**. Dead read flat at 50 MB. One tmux client for list/read/stop. | 0 |
| 6 | `find` ≤50 ms p95 | 0 |
| Per ask | Default deadline 1200 s, cap 3500 s. ≤2 invocations, no auto retry. | claude `--max-budget-usd 1.00` (config). codex: wall clock only. |

**What to record, so estimates become measurements:**
- **Per delegate:** `meta.json` (`agent`, `exit`, `wall_s`, `cost_usd|null`, tokens or null, `brief_bytes`) and `status.json` (`state`, `bytes`, `sha256`, `stamped_at`).
- **Per tmux move:** the bench JSON (5a): p50/p95/p99, failures, clients per call, and the case bucket. In live use, read the adapter's existing span log (`$GRIM_INSTRUMENT_FILE`).
- **New records hold counts and states only:** no briefs, pane text, raw argv or secrets (CX §5).

**Pass gate** (each phase states which rows apply):
- ≥300 warm samples per tmux move, with p50, p95 and p99 reported. p99 counts as observed only with ≥300 samples.
- The 100-window, 50 MB-log and parallel-same-name cases are reported separately.
- tmux client count is printed next to each latency.
- `grep -n "time.sleep" src/grim/seeds/bodies_delegate.py src/grim/seeds/bodies_headless.py src/grim/seeds/bodies_compare.py` prints nothing.
- No `wait-for` child survives a test (`pgrep -f "wait-for grim-"` is empty).
- CLI end-to-end numbers (`grim run …` from a shell) are reported, but **do not gate**. Fixing them needs the frozen `cli.py` (§8 Q4).

## 7. Explicitly out of scope

- **Shared tmux prelude, driver superclass, window-opener extraction:** `ABSTRACTIONS.md` ("No-shell refusal block, three copies", "…now three copies") and root `CLAUDE.md` §2 say flag, never extract. 2a reuses the opener through `grim run tmux_start` instead.
- **Daemon, PID table, job registry file:** `surfaces.md` §3.2 and §3.3; `seeds/CLAUDE.md` ("tmux seeds keep no registry").
- **Event bus, router class:** root `CLAUDE.md` §2 ("never an in-process event bus") and §3 ("Boring code"). Routing is prompt rules plus three seeds.
- **Format-string caching, micro-batching:** worth ≈2 ms, per the measurements in CL §5 and S §4.
- **Embeddings:** `docs/build-plan.md` Phase 6 is gated on measurement, and `verbs/find.py:1-2` agrees. Phase 6 builds the lexical baseline first.
- **Delegate-written scripts entering the library unproven:** `07-recipes.md` ("A probe stays a one-off") and S §4. A `## Reusable` capture step is a later plan.
- **Also deferred:**
  - generation IDs and opaque handles (decision c);
  - cohort leases (d);
  - `@grim_turn` hooks for TUI delegates (UNVERIFIED);
  - claude `--max-turns` (absent on 2.1.322);
  - `system_check` probes (CX P12);
  - removing the `tmux_send` paste settle (UNVERIFIED on real TUIs);
  - two writers fanned out into one checkout (S §1.7).

- **Phase 5 — tmux latency is a product feature (done, 2026-11-19).** `run(name="tmux_list")`/`tmux_read`/`tmux_send`/`tmux_stop`/`tmux_start` are now fast enough to drive a live fleet: p99 list 55.1 ms, read 68.2 ms, send 72.8 ms, stop 63.2 ms, all under the 100 ms budget; `tmux_start` p99 144.6 ms is still over budget (11 tmux clients per start) and is the open item. Bounded `tmux_read` (4 MiB log tail + LINES/SKIP), a per-name non-blocking flock so a taken job name fails fast instead of spawning a second window, an argv-exact exec stub so a job's command line is never re-parsed by a shell, `run_dir_prune` for run-dir cleanup, and uv fingerprinting by stat. Four Feature 1 boxes ticked. §3.3 "Nothing is held open" is proven for the tmux seeds only, not the browser seeds.

## 8. Open questions for the human (each has a default; say "go" to take them all)

1. **Headless delegate powers.** Recommended default, added to `~/.grimoire/config.toml` by the human:
   ```toml
   [delegates.claude]
   bin = "/opt/homebrew/bin/claude"
   exec_args = ["--tools", "Read,Glob,Grep", "--max-budget-usd", "1.00", "--no-session-persistence"]
   [delegates.codex]
   bin = "/opt/homebrew/bin/codex"
   exec_args = ["--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral"]
   ```
   `exec_args` is required: without it `agent_ask` refuses to start.
2. **Paid smoke runs** in Phase 2: one 3-line brief per agent, ≤$0.50 total. Default: yes.
3. **Exit-code change for plain jobs:** `tmux_wait` exits 3 when a job died with a non-zero exit or was stopped. Default: yes. It is the same false-success class.
4. **CLI start-up** (114–194 ms) needs the frozen `cli.py` (lazy imports). Default: gate R3 on the in-process adapter number only. Report the CLI number, and leave `cli.py` as a separate human task.
5. **Retire `claude_tmux`/`codex_tmux`** after 5 real successful `agent_ask` runs. Default: yes; the human archives them, since they are `seeded=0`.

---

## Status

- **2026-11-20 — Phase 4 shipped** (`feat/teach-route`, merged as `604cfbd`).
  The rendered system prompt now routes `agent_ask -> agent_wait -> agent_compare`
  as the three-call delegation path and says tmux requests and routine system
  requests are never delegated; `agent_start` is documented as attended pairing
  only. Every observation carries a `jobs:` line (one `tmux list-windows -F`,
  <=8 jobs/300 chars, never raises; `grim-agent --no-jobs` turns it off), so
  "what is running" costs zero tool calls. Measured jobs-line cost on the real
  server: p50 7.5 ms, p95 9.2 ms (budget <=15 ms). Goldens pin the rendered
  prompt (jinja2 + StrictUndefined against a freshly `grim init`-ed library) and
  an offline replay drives brief -> agent_ask -> agent_wait -> agent_compare
  through `GrimEnvironment.execute` with a fake claude+codex on a private tmux
  server. 797 tests green on the branch and on `main`; ruff + mypy clean.
  Checked in as approved: the adapter's new subprocess call (the jobs line) is
  the third sanctioned case in `src/grim/adapter/CLAUDE.md`.
  Deliberately left open: §3.2 "Listing asks the surface" (needs a test that
  traps file/DB reads), and the health of a suite started with
  `GRIM_BASE_SEEDS` set in the caller's env (15 pre-existing failures; the
  hermetic fixture should drop that variable too). The static fallback roster in
  `grimoire.yaml` (only used when `grim_seeds` is undefined) still predates
  agent_ask/agent_wait/agent_compare.
 — Phase 1 built and verified (2026-11-19)

- **2026-11-20 — Phase 3 shipped.** `agent_wait SECONDS JOB...` (MAX_JOBS 4, one
  `list-windows` + `status.json` per job, `Popen(tmux wait-for)` + `selectors.select`,
  no `time.sleep` — a grep test proves it, exit 0 all DONE / 3 terminal-with-failure /
  124 still pending, no orphaned `wait-for` clients) and `agent_compare A B` (pure
  python, evidence-checked claims, 4 KiB cap, exit 0/3/1) are merged to `main`
  (`feat/one-wait`, 5 commits, **788 tests green**, ruff + mypy clean).
  Any number of delegates is now one blocking call, and two answers are arbitrated
  without spending a token. Also on main: `3f889ec`, the hermetic-suite fixture
  (`GRIM_SESSION*`, `GRIM_CWD`, `GRIM_CALL_DEPTH`, `GRIM_MODEL` no longer leak from a
  live session into the tests), and `agent_exec` now records `dir` in `meta.json`
  so evidence paths can be checked.
  Two checklist boxes stay open on purpose: a seed's wait cut by the run timeout, and
  the caps on `agent_start` / the web seeds. Remaining work: phase 4 (teach the prompt
  the `agent_ask` -> `agent_wait` -> `agent_compare` route and add the jobs line),
  then a local reinstall + `grim init` so the new seeds reach the live library.


Branch `feat/honest-completion`, 6 commits on top of `185baa4`, working tree clean:

| Step | Commit | What landed |
|---|---|---|
| 1a | `6294f74` | `TMUX_READ` moved verbatim to `seeds/bodies_tmux_read.py`; `bodies_tmux.py` 487 → 437 lines; `TMUX_SEEDS` unchanged, so `grim init` sees no drift |
| 1b | `a893b4f` | characterization tests pinning the two old behaviours |
| 1c | `ecfdf2e` | whitespace-flattened dialog match ported into `bodies_agent.py` (the live `agent_start@3` patch is now in source; `grim init` can no longer revert it) |
| 1d | `0cc63ea` | `agent_done` in the new `seeds/bodies_delegate.py` (`DELEGATE_SEEDS`), new `agent_start` done contract, `agent_read` = DONE-only-if-checked / `UNCHECKED` / EXITED exit 3 |
| 1e | `6a08508` | `tmux_wait` honours `@grim_kind` + `@grim_done`: stamped → 0 at once; bad end (dead delegate with no stamp, non-zero exit, stopped mid-wait) → 3 |
| — | `3e32966` | 4 checklist boxes ticked, each naming its proving test |

Independent verification (not the delegate's word): `uv run pytest -q` → **734 passed**;
`ruff check` clean; `gil log main..HEAD` = the 6 commits above; `bodies_delegate.py` 110 lines,
`bodies_tmux.py` 437, `bodies_agent.py` 344 — all under the 500 cap.

**Hermeticity caveat found while verifying:** run from *inside* a grim session, the suite reports
4 false failures (`adapter/test_environment.py` session-metadata tests, `verbs/test_shared.py`)
because the ambient `GRIM_MODEL` / `GRIM_SESSION*` / `GRIM_CALL_DEPTH` env vars leak into the
fixture. With those stripped: 734 passed. Worth a small fix (clear/ignore ambient `GRIM_*` in the
test fixture) — not a Phase 1 regression.

**Two human-gated items before Phase 2:**
1. `grim init` against the real library would publish `agent_start@4`, `agent_read@2`,
   `tmux_wait@2` and the new `agent_done` to `~/.grimoire/grimoire.db`. Not done — the global
   install is the human's call.
2. Phase 2's manual paid smoke run (`agent_ask both smoke-1`, ≤$0.50) pins the UNVERIFIED JSON
   field names; the plan's default is "yes".
