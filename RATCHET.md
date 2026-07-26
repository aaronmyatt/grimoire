# Ratchet board

Debt ledger for `/ratchet` to burn down, one rule × one slice per session.
Counts are recomputed from the tool baselines, never hand-edited here.

## Debt ledger (at `/setup` time, 2026-07-26)

| Category | Count | Source |
|---|---|---|
| mypy strict-mode errors | 0 | `.claude/mypy-baseline.txt` (empty — greenfield repo) |
| ruff lint violations | 0 | no baseline file exists — ruff has no bulk-suppression mechanism; `/ratchet` will use per-violation `ruff check --add-noqa` or `pyproject.toml` per-file-ignores if debt ever accrues |
| Slice-boundary violations | 0 | `.claude/scripts/check-boundaries.sh` |
| File-length violations | 0 | `.claude/scripts/check-file-lengths.sh` |
| Coverage | 0% (floor, ratchets up only) | `pytest --cov` |

Nothing to promote yet — this is a fresh scaffold, not an audited legacy
codebase. The first real `/ratchet` session on this repo should wait until
Phase 0/1 of the build plan (the six verbs) lands with real code and tests.

## Priorities

(empty — nothing baselined to prioritize yet)

## Campaigns in flight

(empty)

## Ejected

(empty — items removed from active tracking with a recorded reason go here)

## Exceptions granted

(empty — every narrowest-scope exception granted against a check gets
recorded here with its justification; an exception with no recorded reason
is a debt with no owner)
