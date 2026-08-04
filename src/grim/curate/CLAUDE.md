# Slice: curate

## Purpose
Human-only library ergonomics — browsing, curation, and shell integration
that sit *beside* the six agent verbs, never inside them. Everything here
reads (and later writes) the same library via `src/grim/db.py`, but is
reachable only through `src/grim/cli.py`, exactly like `init`/`config`/
`doctor`. This is the home the "human-only management, read-only agent
bias" decision gives the favourites/near/completion work.

## Public interface
One module per command, each a single `cmd_*(args)` entry point dispatched
by `src/grim/cli.py`:
- `near.py` — `grim near NAME`: scripts that tend to run adjacently to
  NAME, read from the emergent `script_affinity` view (both directions).
- `recent.py` — `grim recent`: the library by last-run time, from
  `script_health`.
- `edit.py` — `grim edit NAME`: round-trips the script's body through
  `$EDITOR` (a real subprocess with inherited stdio, not `_invoke`'s
  captured dispatch — a genuine interactive session whether launched from
  a real shell or from `grim-agent`'s `/edit` slash command). On change,
  resolves a changelog (`--changelog` override -> AI one-liner, lazily
  calling `litellm` only if the optional `agent` extra is installed and a
  model is configured -> one manual prompt -> a generic fallback — never
  blocking or crashing) and persists a new `script_version`, duplicating
  `verbs/update.py`'s lint-then-insert logic rather than importing it.
- `tags.py` — `grim tag NAME TAG...` / `untag` / `tags` (list all tags +
  usage counts) / `tagged TAG` (scripts carrying it), read/written through
  the `tag`/`script_tag` junction (migration 0002). Tag names are
  normalized lowercase and validated against `TAG_RE`. Idempotent:
  re-tagging or untagging is a no-op, never an error. A tag with zero
  scripts still exists as a row (`scripts_for_tag` returns `[]`) —
  `LookupError` is reserved for a tag that was never created at all, so a
  typo in `grim tagged` is distinguishable from "nothing tagged this yet."

`_shared.py` (`connect()`, `resolve_script_version()`, `lint()`,
`body_hash()`) is internal, not public surface — deliberate copies of
`verbs/_shared.py`'s equivalents.

## Invariants
- **Never wired into `adapter/tools.py::GRIM_TOOLS`.** That omission is
  what keeps this slice human-only — the agent cannot call anything here.
  Adding a curate command to `GRIM_TOOLS` would silently widen the
  load-bearing six-verb fence and is out of bounds. This is why `edit.py`
  can safely call an LLM directly (unlike the six agent verbs) — no
  autonomous loop can ever trigger it, only a human explicitly running
  `grim edit` or typing `/edit` themselves.
- Slices never import each other (root CLAUDE.md §2): curate does not
  import `verbs/`. `_shared.py`'s helpers are deliberate copies of
  `verbs/_shared.py`'s, not a shared-kernel promotion.
- All DB access goes through `src/grim/db.py`; no module here opens its
  own SQLite connection directly.
- External input (a script NAME) is validated, not asserted — an unknown
  name yields a clean nonzero exit, never an `AssertionError`.
