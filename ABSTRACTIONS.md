# Abstraction ledger

Append-only. An entry records what is duplicated, where, and what the
abstraction might be — it does not perform the extraction. The human
decides if and when extraction happens, as its own task. See root
`CLAUDE.md` §2 "Avoid hasty abstractions."

Never edit or delete a prior entry; append only. The fence enforces this
mechanically.

## Entries

- **Language catalog, three copies.** The extended-language catalog now
  exists with different shapes in three places: `exec/dispatch.py`'s
  `_EXTENDED_RUNNERS` (suffix, argv builder, version probe, platform gate),
  `curate/edit.py`'s `_LANGUAGE_SUFFIX` (suffix only, for the editor temp
  file), and `seeds/bodies.py`'s export `EXT` (dot-less extension, for
  export filenames). A single suffix/extension source (shared kernel or a
  generated table) would remove the drift risk when a language is added —
  but slices never import each other, so extraction must wait for the
  human's call as its own task.
- **GRIM_LANGUAGES env parse, two copies.** `exec/dispatch.py` and
  `adapter/tools.py` both parse the comma-joined `$GRIM_LANGUAGES` env var
  (the adapter's copy skips platform filtering by design, so the two sets
  can differ on a wrong-OS language). A tiny shared kernel helper would
  unify them if a third consumer appears.
- **smevals run-grim preamble, four copies.** All four eval runners
  (`evals/grim-{smoke,debug,solve,plan}/run-grim` share three; `evals/
  grim-swebench/run-grim` is the fourth, diverging on GRIM_DB precedence —
  it adds GRIM_EVAL_COLD/grim_group — and on stdout carrying the patch
  instead of the answer) repeat the same blocks: required-env asserts,
  GRIM_DB isolation precedence, GRIM_TRAJ_DIR export, GRIM_LANGUAGES pin,
  the word-split `$GRIM_AGENT_CMD -p` launch, and the exit-2/infra-fail
  convention. The abstraction might be a sourced `evals/lib/run-grim.sh`
  prelude — but each eval is deliberately self-contained (the same
  independence rule as slices), so extraction waits for the human's call
  as its own task.
- **run-metrics observer checker, five identical copies.** Byte-identical
  `checkers/run-metrics` (+ selftest) in all five evals — deliberate:
  evals never reference each other's files, so the metric definitions
  ship as copies. Drift risk is real (a definition change must touch all
  five); a `diff` across the copies is the cheap audit until the human
  decides on extraction as its own task.
- **run-mini extends the run-grim preamble family.** grim-swebench's
  baseline runner duplicates run-grim's env asserts, mirror/clone/
  checkout, and patch-emission blocks (arms must not drift — the
  comparison is agent vs agent). Any future sourced-prelude extraction
  should cover it together with the entry above.
