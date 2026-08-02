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

`_shared.py` (a `connect()` with `row_factory` set) is internal, not
public surface.

## Invariants
- **Never wired into `adapter/tools.py::GRIM_TOOLS`.** That omission is
  what keeps this slice human-only — the agent cannot call anything here.
  Adding a curate command to `GRIM_TOOLS` would silently widen the
  load-bearing six-verb fence and is out of bounds.
- Slices never import each other (root CLAUDE.md §2): curate does not
  import `verbs/`. `_shared.connect()` is a deliberate copy of
  `verbs/_shared.connect()`, not a shared-kernel promotion.
- All DB access goes through `src/grim/db.py`; no module here opens its
  own SQLite connection directly.
- External input (a script NAME) is validated, not asserted — an unknown
  name yields a clean nonzero exit, never an `AssertionError`.
