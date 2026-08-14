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
- **GRIM_BASE_LANGUAGES parse joins the GRIM_LANGUAGES two-copy.** The
  builtin-subsetting knob is parsed in `exec/dispatch.py`
  (`base_languages`) and `adapter/tools.py` (`lang_enum`) — the same
  deliberate pair as the earlier GRIM_LANGUAGES entry, same reason
  (slices never import each other), now with a matching never-empty
  fail-safe in both. Any future unification covers all four parses.
- **Sanitized patch capture, two copies.** `evals/grim-swebench/run-grim`
  and `run-mini` now share a guarded diff-emission block: venv/tooling
  pathspec excludes, a binary-blob refusal (exit 1 = infra, not a model
  miss), and the empty-diff-stays-empty rule. Duplicated deliberately —
  the arms must not drift, and both belong to the run-grim preamble
  family above; any future `evals/lib/` prelude extraction should absorb
  this block too.
- **FTS match-query builder + bm25 column weights, three copies.** The
  tokenize-into-FTS5-MATCH helper now exists in `verbs/_shared.py`
  (`fts_match_query`, OR'd quoted tokens), `adapter/agent.py`
  (`_match_query`, same shape), and `adapter/completer.py`
  (`_fts_prefix_query`, prefix-starred variant for partial words) — and the
  `bm25(script_fts, 10.0, 5.0, 1.0)` name>description>body weight triplet
  is repeated in agent.py and completer.py. Deliberate: slices don't share
  internals, and the two adapter copies differ on prefix semantics. The
  abstraction might be a shared-kernel `fts_query(text, *, prefix=False)`
  plus a named weights constant — the human decides, as its own task.
- **Tag upsert, two copies.** The two-statement tag attach (INSERT OR
  IGNORE into `tag`, then into `script_tag` via SELECT) exists in
  `curate/tags.py` (`add_tags`, human-driven, normalizes via `TAG_RE`)
  and now in `verbs/_shared.py` (`stamp_repo_tag`, the write-time
  `repo-<name>` provenance stamp, shape guarded by its own
  `_REPO_TAG_RE` mirroring curate's). Deliberate: slices don't import
  each other, and the verbs copy is fixed-prefix/no-user-input. The
  abstraction might be a shared-kernel `attach_tag(conn, script_id,
  tag)` — the human decides, as its own task.
