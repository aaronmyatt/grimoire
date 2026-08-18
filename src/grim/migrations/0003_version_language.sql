-- Schema v3 — per-version language (verbs/CLAUDE.md).
--
-- Language was a per-script property, fixed at `grim write` time and
-- unchangeable thereafter: `grim update` linted every new body against
-- script.language and had no way to override it. An agent that wanted to
-- rewrite a bash script in python could not, and worked around it by forking
-- a near-duplicate `*_py` sibling — the exact duplication the library exists
-- to prevent.
--
-- Moving language onto the version makes it changeable without rewriting
-- history. script.language stays authoritative for "what language is this
-- script NOW" (find/list headers, the writable-set gate); each version
-- additionally records the language its body was written in and linted
-- against, so `grim run name@1` still dispatches the old body to the old
-- interpreter after a language change.
--
-- Backfill: before this migration every version was, by construction, written
-- under its script's single language.

ALTER TABLE script_version ADD COLUMN language TEXT;

UPDATE script_version
   SET language = (
     SELECT s.language FROM script s WHERE s.id = script_version.script_id
   );
