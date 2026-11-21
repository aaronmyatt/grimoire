# Slice: seeds

## Purpose
Seed script bodies loaded on `grim init` (`run_cmd`, `run_py`, `read_file`,
`write_file`, `edit_file`, `apply_patch`, `grep_tree`, `list_dir`,
`stats`, `gardener`, `export_library` — build plan §3 Phase 3, D11).
Meta-tooling ships as library scripts here, not as new CLI verbs. The
answer to "long-lived work" is a tmux job, not a longer `grim run` timeout
(which is hard-capped, see verbs/run.py); job logs go under
`$GRIM_RUN_DIR` (default `~/.grimoire/run`). The old pid-file trio
(`run_bg`/`list_bg`/`stop_bg`) is retired. Program-starting bodies live in
`bodies_exec.py` and `bodies_tmux.py` and never use a shell: argv only,
shell operators and shell interpreters refused.
Surface drivers ship here too (docs/surfaces.md). The first is tmux, in
`bodies_tmux.py`: `tmux_start`/`tmux_list`/`tmux_read`/`tmux_send`/
`tmux_stop` (docs/features/01-jobs.md) — a job is a tagged tmux window
in session `grim`; these replaced the background trio — and `tmux_wait`
(docs/features/02-waiting.md): block on a named signal instead of
polling; every job signals `grim-JOB` when it ends. `tmux_start --kind agent`
tags the window as a delegate (allowlist: `job`, `agent`).
The second driver is the delegate one, in `bodies_agent.py`
(docs/features/04-delegates.md): `agent_start` runs claude, codex or pi as
a TUI in a tmux job tagged `@grim_kind=agent` and pastes it a brief that
ends with the done contract (write `<run_dir>/JOB/result.md`, then run
`agent_done JOB` from `bodies_delegate.py`: it checks the file, writes `status.json`,
stamps `@grim_done`, signals `grim-JOB`); `agent_read` says DONE only for a checked answer. Wait,
list, follow-up and stop are borrowed from the tmux seeds.
Headless delegates: `agent_exec --exec-args JSON AGENT JOB DIR DEADLINE`
(`bodies_headless.py`) runs `claude -p` or `codex exec` on
`<run_dir>/JOB/brief.md` with powers stated in its argv (never read from
config), and hands a good answer to `agent_done`; any other end (TIMEOUT:
TERM to the child's process group, KILL 2 s later; FAILED) is written to
`status.json` in agent_done's shape, exit 3. AUTH, CONTEXT, FAILED and
INVALID_RESULT are told apart there; `meta.json` holds cost and tokens.
`agent_ask --exec-args JSON claude|codex|both ID DIR [SECONDS]` (brief on
stdin) checks everything first, then opens `ID-AGENT` per agent through
`grim run tmux_start --kind agent`, each running `agent_exec` with one shared
deadline; the brief gets the answer headings `agent_done` checks appended.
`agent_wait SECONDS JOB...` (`bodies_delegate.py`) blocks once for up to 4
jobs: one `tmux wait-for` client per running job, watched with `selectors`,
durable `status.json` checked before and after every wake; never `time.sleep`.
Exit 0 all DONE, 3 any failed, 124 any still running (no client left behind).
`agent_compare JOB_A JOB_B` (`bodies_compare.py`) is the mechanical arbiter:
no model, state from `status.json` only, claims matched by word overlap,
`path:line` evidence checked under `meta.json` `dir`, commands never run; one
packet of at most 4096 bytes. Exit 0 both DONE, 3 one, 1 neither.
`agent_stats [DAYS]` (same file; default 30, by `status.json` mtime) scores each agent from
`status.json`/`meta.json`: asks, DONE rate, median wall and cost (no cost above 0 → `unknown`),
failures per state; a job with no `status.json` is `no-status`, never a crash.
`run_dir_prune [--keep-days N] [--max-mb M] [--yes]` (`bodies_prune.py`) frees
the run dir: it deletes only `JOB.log` files and `JOB/` dirs older than N days
(default 7) whose tmux window is gone, oldest first, stopping under M MB; without
`--yes` it only lists. If tmux cannot be asked, it deletes nothing.
The third is the browser driver (docs/features/06-browser.md):
`web_open` (python, `bodies_web.py`) runs one long-lived Chrome as tmux
job `chrome` on its own profile and opens tabs; `web_read`, `web_eval`,
`web_click`, `web_type` and `web_shot` (Go, `bodies_web_go.py`) attach to
the current tab with chromedp and are built once by exec/gobuild.py.

## Public interface
`loader.py` — `load_seeds(db) -> list[str]`: bare names for newly written
seeds, `name@version` for present-but-drifted seeds re-synced with the
bundled copies, `[]` when fully in sync. The only function anything
outside this slice calls; individual seed bodies are data, not exported
symbols.

## Invariants
- `load_seeds` is idempotent — safe to call against a database that
  already has these rows (re-running `grim init` must not duplicate or
  error). Idempotence is an explicit name lookup, never an exception
  swallow — any write_script rejection fails `grim init` loudly.
- `load_seeds` converges an existing library on this build's seed set: a
  present seed whose latest body differs from the bundled copy gets a new
  append-only version (via the update verb, changelog included — local
  divergence stays recoverable in history), and a drifted description is
  updated in place. Rows the human took over (`seeded=0`) or archived are
  never touched.
- Seeding ignores the language toggles (GRIM_LANGUAGES /
  GRIM_BASE_LANGUAGES): those gate agent writing, not the stdlib. Which
  seeds land is gated only by `GRIM_BASE_SEEDS` (unset = the full
  roster, so the default library floor is identical in every
  environment): a comma-separated subset of bundled names seeds — and
  converges — only those; explicitly empty seeds nothing (a control
  arm); an unknown name fails `grim init` loudly before any write. The
  knob gates seeding only — exclusion never removes or archives a seed
  already present.
- Every seed is written with `seeded=1`, `scope='global'`.
- Seed bodies are plain scripts stored as `script_version.body` rows —
  this slice never gives seeds special execution privileges over
  agent-authored scripts.
- `bodies.SEEDS` is the one roster everything reads. `bodies_tmux.py` adds
  to it through plain `(name, description, body)` rows (`TMUX_SEEDS`) and
  never imports `bodies.py`.
- tmux seeds keep no registry of their own: jobs are found by window tags
  (`@grim_kind`, `@grim_job`, `@grim_started`), asked of tmux each time.
  They touch only session `grim` (exact target `=grim`) on the default
  server, or on the private server `$GRIM_TMUX_SOCKET` names (tests always
  set it). A job's program is exec'd as argv, never through a shell; the
  one shell line is the seed's own fixed `cat >> LOG` for `pipe-pane`.
  `tmux_start` hands tmux only a fixed `python -c` exec stub plus the job's
  argv as base64 JSON, so tmux never re-parses it (a trailing `;`, a lone
  path with a space); a sole token that is an executable file is never split.
  `tmux_start` holds a non-blocking `flock` on
  `<run_dir>/.tmux-start-<server>-<JOB>.lock` from its lookup to its last tag; a
  second start of that name meanwhile exits 1 "name is being started". The
  lock is a guard, not a registry: tmux still says what exists.
- A delegate's launch flags and program path come only from
  `[delegates.AGENT]` (`args`, `bin`) in `~/.grimoire/config.toml` (or the
  file `$GRIM_DELEGATES_CONFIG` names; tests always set it). `agent_start`
  takes no argument for them, and nothing in a brief can change them.
  Start-up dialogs are answered from a fixed per-agent table; a login
  screen is never answered — it fails loudly for a human.
- A headless delegate's powers are stated on every call or it does not run:
  `--exec-args JSON` (`{"claude": [...], "codex": [...]}`), never read from
  config; missing → exit 2 "powers must be stated". `delegates.toml` documents
  the least powers that work for read-only research (nothing reads it): claude
  `--tools Read,Glob,Grep --max-budget-usd 1.00` (`-p` denies unlisted tools);
  codex `--sandbox read-only --skip-git-repo-check --ephemeral`.
- The browser seeds drive only grim's own Chrome: profile
  `$GRIM_CHROME_DIR` (default `~/.grimoire/chrome`; a daily Chrome profile
  is refused), DevTools on `127.0.0.1:$GRIM_CHROME_PORT` (default 9222),
  and a port held by a Chrome grim did not start is refused. State stays
  in Chrome: the current tab is the page Chrome lists first, and every Go
  seed detaches without closing it. A secret is typed by env var NAME
  (`web_type SEL --env NAME`), never passed as text. The Go seeds are one
  shared Go block (`_GO_SHARED`) plus a `main` each, written for chromedp
  v0.21.0 — the version `web_open` pins in the shared Go module.
- `loader.RETIRED_SEEDS` (today: `shell`, which ran `/bin/sh`, and the
  pid-file trio `run_bg`/`list_bg`/`stop_bg`) names seeds
  this build no longer ships. `load_seeds` archives such a row only while
  it is still the bundled seed (`seeded=1`, unarchived) and reports it as
  `name (retired)`; a taken-over row (`seeded=0`) is never touched, and
  nothing is ever deleted. A retired name is never also in `SEEDS`.
