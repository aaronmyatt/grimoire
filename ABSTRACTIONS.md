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
