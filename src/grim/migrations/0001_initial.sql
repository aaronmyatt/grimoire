-- Schema v1 — build plan §3. Applied once by db.migrate() and recorded in
-- schema_migrations (that bookkeeping table is created by db.py itself,
-- not here, since the migration runner depends on it existing first).

CREATE TABLE session (
  id          TEXT PRIMARY KEY,          -- uuid or 'human-adhoc'
  kind        TEXT NOT NULL,             -- 'agent' | 'human'
  task        TEXT,                      -- task text for agent sessions
  model       TEXT,
  repo_fingerprint TEXT,                 -- git remote+root hash, if in a repo
  started_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE script (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,      -- slug: ^[a-z][a-z0-9_]{2,63}$ — this is the API
  language    TEXT NOT NULL,
  description TEXT NOT NULL,             -- mandatory; the retrieval surface
  scope       TEXT NOT NULL DEFAULT 'global',
  parent_version_id INTEGER REFERENCES script_version(id),  -- fork lineage
  origin_session_id TEXT REFERENCES session(id),            -- provenance
  seeded      INTEGER NOT NULL DEFAULT 0,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE script_version (
  id          INTEGER PRIMARY KEY,
  script_id   INTEGER NOT NULL REFERENCES script(id),
  version     INTEGER NOT NULL,
  body        TEXT NOT NULL,
  body_hash   TEXT NOT NULL,             -- sha256; cheap exact-dup detection
  changelog   TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  UNIQUE (script_id, version)
);

CREATE TABLE execution (                 -- the append-only event log
  id          INTEGER PRIMARY KEY,
  script_version_id INTEGER NOT NULL REFERENCES script_version(id),
  session_id  TEXT NOT NULL REFERENCES session(id),
  seq         INTEGER NOT NULL,          -- position within session
  argv        TEXT,                      -- JSON array
  stdin       TEXT,
  cwd         TEXT,
  exit_code   INTEGER,
  stdout      TEXT,
  stderr      TEXT,
  duration_ms INTEGER,
  env_fingerprint TEXT,                  -- interpreter versions, for staleness triage
  started_at  TEXT DEFAULT (datetime('now')),
  UNIQUE (session_id, seq)
);

-- Standalone (non-external-content) FTS5 table, so it survives regardless
-- of how script/script_version evolve. rowid is pinned to script.id (not
-- script_version.id) so a search hit always resolves to exactly one script
-- row, backed by whichever version is currently latest — "latest version
-- wins" per the schema design note.
-- Ref: https://www.sqlite.org/fts5.html
CREATE VIRTUAL TABLE script_fts USING fts5(name, description, body);

CREATE TRIGGER script_fts_ai_version AFTER INSERT ON script_version BEGIN
  INSERT OR REPLACE INTO script_fts(rowid, name, description, body)
  SELECT s.id, s.name, s.description, new.body
  FROM script s
  WHERE s.id = new.script_id;
END;

CREATE TRIGGER script_fts_au_script AFTER UPDATE OF name, description ON script BEGIN
  INSERT OR REPLACE INTO script_fts(rowid, name, description, body)
  SELECT new.id, new.name, new.description, sv.body
  FROM script_version sv
  WHERE sv.script_id = new.id
  ORDER BY sv.version DESC
  LIMIT 1;
END;

-- "called before/after" is emergent, never bookkept:
CREATE VIEW script_affinity AS
SELECT va.script_id AS a, vb.script_id AS b, COUNT(*) AS times_adjacent
FROM execution ea
JOIN execution eb  ON eb.session_id = ea.session_id AND eb.seq = ea.seq + 1
JOIN script_version va ON va.id = ea.script_version_id
JOIN script_version vb ON vb.id = eb.script_version_id
GROUP BY 1, 2;

CREATE VIEW script_health AS               -- feeds find/list ranking + gardener
SELECT s.id, s.name, COUNT(e.id) AS runs,
       AVG(e.exit_code = 0) AS success_rate,
       MAX(e.started_at)    AS last_used
FROM script s
LEFT JOIN script_version v ON v.script_id = s.id
LEFT JOIN execution e      ON e.script_version_id = v.id
GROUP BY s.id;
