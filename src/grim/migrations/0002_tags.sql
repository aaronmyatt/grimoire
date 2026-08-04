-- Schema v2 — tags (curate/CLAUDE.md: human-only library ergonomics).
-- "favourite" is not a separate column: starring a script is just tagging
-- it with the well-known tag "favourite" (curate/tags.py's FAVOURITE_TAG),
-- so there is exactly one mechanism for "this script is special," not two.

CREATE TABLE tag (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,     -- normalized (lowercase, slug-like)
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE script_tag (                -- many-to-many join
  script_id   INTEGER NOT NULL REFERENCES script(id),
  tag_id      INTEGER NOT NULL REFERENCES tag(id),
  created_at  TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (script_id, tag_id)
);

CREATE INDEX idx_script_tag_tag ON script_tag(tag_id);
