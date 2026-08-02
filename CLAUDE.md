---
template: setup-constitution
template-version: 1.1.0
generated: 2026-07-26
source: .claude/budgets.json
---


# Engineering Policy — grimoire

This file is **generated**, not hand-authored. Its numbers come from
`.claude/budgets.json`; its structure comes from the `/setup` skill's
constitution template (the version in this file's frontmatter). To change
a number: edit `.claude/budgets.json` and re-run `/setup` — it shows the
diff to this file before writing, never rewrites silently.

These rules are hard constraints, not suggestions. If a rule must be
broken, stop and say so explicitly with a one-line justification — never
silently deviate. Priorities, in order: correctness, simplicity,
reviewability, performance. The scarcest resource here is human review
attention — optimize everything for parseability.

## 1. Budgets — constraints are first-class citizens

Budgets are features, designed before the code that must fit inside them.
A new capability is declared with its budget or it is not built.

| Budget | Limit | Enforced by |
|---|---|---|
| Function length | ≤ 60 lines | lint |
| Module/file length | ≤ 500 lines, hard cap | lint |
| Change size | ≤ 300 changed lines — universal | stop gate + CI |
| Parameters per function | ≤ 4 | lint |
| Nesting depth | ≤ 4 | lint |
| Cyclomatic complexity | ≤ 10 | lint |
| Line width | ≤ 100 cols | formatter |
| Assertions per function | ≥ 2 | review + heuristic lint |
| Response payload | ≤ 140 KB (≤ 42 KB critical path) | edge + CI budget check |
| Local round trip | < 100 ms p99 | perf test |
| Coverage floor | 0%, never regresses | test runner + CI |
| Queues, caches, buffers, retries, collections | explicit max, always | review + assert |

Budgets may be tuned via budgets.json, but never deleted — only
re-justified. Every budget exists in executable form — a lint rule,
middleware assert, or CI check that fails loudly. A number here always
matches the same field in budgets.json, the hooks, the lint config, and
CI; `/setup`'s drift audit flags any disagreement as a bug, not a style
choice.

## 2. Slices — the unit of everything

The codebase is vertical slices: one directory per feature under
`src/grim/`, each owning its handler, UI, state, templates, and
tests. A slice is self-contained.

- Slices NEVER import each other. Not even "just this once." A slice
  depends on the shared kernel or on nothing.
- A slice is reached only through its public surface (`index` export /
  module boundary). No deep imports.
- The shared kernel (`src/grim/`) is tiny, frozen, and versioned
  like a third-party library. Agents do not edit it as a side effect of
  anything; changes to it are human-initiated tasks with their own
  review, and every write to it is individually confirmed by the fence.
- **Duplication between slices is a design choice, not a defect.** If two
  slices need similar logic, copy it. Two slices owning similar code is
  independence, not debt. Nothing "moves to shared" as a reflex.
- Within a slice: plain function calls. Across slices: the URL, the
  parent/page, or a message across a time boundary (queue, SSE) — never
  an in-process event bus. Every message/event type is declared in the
  single registry file, so all indirection is enumerable in one place.
- Each slice has its own CLAUDE.md (purpose, public interface,
  invariants). Read it before touching the slice; update it when the
  interface changes.

### Avoid hasty abstractions

Prefer duplication over the wrong abstraction. No extraction before three
concrete copies exist — and even at three, do not act:

- **FLAG, don't extract.** `ABSTRACTIONS.md` is an append-only ledger of
  *opportunities spotted*, not a registry of abstractions introduced.
  An entry records: what is duplicated, where, and what the abstraction
  might be. The human decides if and when extraction happens, as its own
  task.
- Extraction as a side effect of another task is forbidden.
- Never edit or delete a prior entry; append. The fence enforces
  append-only mechanically (an edit that doesn't preserve the file's
  existing content as a prefix is asked, not silently allowed).

## 3. Code rules (TigerStyle core)

**Shape.** Functions ≤ 60 lines — split by extracting pure
helpers, not by compressing style. Files ≤ 500 lines.
≤ 4 params (bundle into an options struct). Nesting
≤ 4 — guard clauses and early returns. Cyclomatic complexity
≤ 10.

**Control flow.** Every loop has an explicit upper bound; `while (true)`
requires a bounding mechanism and a justifying comment. No recursion
unless depth is asserted against a cap. Switch/match over enums is
exhaustive — no silent default. Boring code: no metaprogramming or
reflection where a plain function works.

**Assertions.** ≥ 2 per function: preconditions on entry,
postconditions before return. Assert loop invariants. Assert negative
space — what must NEVER be true. Assertions are side-effect free. Assert
internal invariants; VALIDATE external input — user/network/file/env data
gets real error handling, never a bare assert. An assertion firing means
"this codebase has a bug," nothing else. Fail fast: in dev and test,
violated invariants crash.

**Data.** Smallest possible scope, declared at first use. Immutable by
default; mutation is opt-in and visible. No global mutable state (in the
browser this includes app-root stores, root provider pyramids, `window`
listeners, and module-level singletons — state lives in the slice or the
URL). No magic numbers; named constants with units in the name
(`timeout_ms`, `size_bytes`). Make invalid states unrepresentable: enums
over flags, distinct types over raw strings/ints. Parse, don't validate —
untrusted input becomes typed values once, at the boundary; interior code
accepts typed values only.

**Errors.** Check every return value and every error. No empty catches,
no broad catch-alls, no log-and-continue for real failures. Handle
meaningfully or propagate — never half-handle. Error messages state what
was attempted and with which values.

## 4. Change protocol — additive over modificative

- One slice per task, one task per session. Work outside the active slice
  is out of bounds — if the task requires it, stop and propose a separate
  task.
- Prefer adding to changing: new function beside old, new slice beside
  old, flip the route, delete the old (strangler fig). A diff that is a
  new folder is the easiest diff to review.
- Before modifying existing behavior: characterization tests first —
  goldens pinning current behavior, landed as their own prior change.
  Then the behavior diff is visible in the test diff.
- Never mix behavior-preserving refactors and behavior changes in one
  commit.
- Diff budget: ≤ 300 changed lines, one concern per change.
  Larger task → split it and say so.
- Plan before code for anything non-trivial: function signatures, stated
  invariants, and the file list — then wait for approval. The invariants
  become the assertions; the file list becomes the fence.
- If anything is ambiguous, ask. Never guess and bury the guess in a
  diff.

## 5. The harness — three layers, none of them vibes

Checks must never interfere with the iteration loop. Blocking happens
before a wrong action (the fence) and before declaring done (the gate) —
never in the middle of thinking. (Claude Code hooks —
https://code.claude.com/docs/en/hooks)

- **`fence.sh`** (PreToolUse — blocks/asks/denies). Edits outside the
  active slice (`$TASK_SLICE`) plus its tests are rejected before they
  happen. Writes to frozen paths are **asked, not denied** — the human
  confirms per write, every time, including during `/setup` itself.
  Baseline/suppression files (`.claude/mypy-baseline.txt`, and anything
  else in `budgets.json.baselinePaths`) are stricter still: **denied
  outright, no confirmation path.** They shrink only through the tool that
  owns them (the same `mypy`-piped regeneration `/setup` used to create
  it, or `/ratchet`'s `prune.sh`) — never a hand edit, not even an
  approved one.
- **`feedback.sh`** (PostToolUse — informs, never blocks). Formats,
  lints, and typechecks the file just touched; findings stream back as
  feedback. Fix them as you go; do not suppress them to quiet the loop.
- **`gate.sh`** (Stop — blocks). A turn cannot end until: formatter,
  linter, and typechecker are clean · the slice's test suite is green
  with output shown · the diff is ≤ 300 lines. A blocked stop
  means fix or split — never bypass.

The harness is convenience for whoever's driving the agent in the moment.
**CI is law** — it re-runs everything the hooks run, for everyone, on
every push.

### Frozen paths

- `src/grim/db.py`
- `src/grim/cli.py`
- `src/grim/config.py`
- `.claude/**`
- `CLAUDE.md`
- `pyproject.toml`
- `uv.lock`
- `.github/workflows/**`

(`ABSTRACTIONS.md` and `RATCHET.md` are additionally append-only for
agent writes, as above.)

### Ratchet rules

Tests, lints, and static analysis must ALWAYS pass — enforced against new
code via baseline-and-ratchet: new violations never; old ones burn down
via `/ratchet`. Strictness only tightens; loosening requires explicit
human override. Never weaken a check to make code pass — no suppressions,
skipped tests, lowered thresholds, or ignore annotations in pursuit of
green. Every granted exception is narrowest-scope, justified inline where
it lives, *and* recorded under **Exceptions granted** in `RATCHET.md` —
an exception with no recorded reason is a debt with no owner.

## 6. Commit discipline

- **Semantic machinery** (hooks, this file, settings.json, lint rule
  choices, CI workflow) — one logical unit per commit, within the diff
  budget. These are the most load-bearing lines in the repo; keep them
  independently revertible (revert the gate without reverting the fence).
- **Generated artifacts** (suppression baselines, lockfiles) — exempt
  from the line budget, always committed alone, never mixed with a
  semantic change. A 2,000-line baseline should never visually bury a
  one-line hook edit.
- Everything else — ordinary code — carries the universal
  ≤ 300-line budget from §1. No other exemptions exist.

## 7. Testing

- Every extracted pure function gets unit tests — that is the payoff of
  the 60-line rule.
- Inject the clock, randomness, network, and filesystem. Tests are
  deterministic: seeded PRNG, fake time, fakes/MSW, no sleeps.
- Anything with an invariant (round-trip, ordering, conservation) gets a
  property-based test.
- Every bug fix ships with a regression test that failed before the fix.
- Slice suites are self-contained: no shared fixtures, no cross-slice
  helpers — shared test utilities are the coupling backdoor.
- Keep the suite fast enough to run on every change.

## 8. Debt is tracked, not hidden

Existing violations at `/setup` time are baselined, not fixed inline —
fixing them is `/ratchet`'s job, one rule × one slice per session. The
board lives at `RATCHET.md`: priorities, campaigns in flight, ejected
items, and granted exceptions. Measured counts live in the tool baselines
and are recomputed, never stored as truth. New code is held to every
budget above immediately; old code graduates through the ratchet.

## 9. Definition of done

Fence never crossed · plan approved (when required) · formatter, linter,
typechecker clean · slice tests green, output shown · every function
≤ 60 lines with its contract asserted · every error
checked · new logic tested · diff ≤ 300 lines · duplication
left in place, opportunities flagged in `ABSTRACTIONS.md` · no new
dependency, no unconfirmed frozen-path write, no weakened check.
