"""Seeds the library on `grim init` (build plan §3 Phase 3). The only
entry point anything outside this slice calls (seeds/CLAUDE.md).
"""

from __future__ import annotations

import sqlite3

from grim.seeds.bodies import SEEDS, SeedSpec
from grim.verbs import update, write

SEED_SESSION_ID = "human-adhoc"  # grim init is a human action
_SYNC_CHANGELOG = "grim init: re-sync with the bundled seed body"


def _sync_seed(conn: sqlite3.Connection, seed: SeedSpec) -> str | None:
    """Converge an already-present seed on the bundled copy. Returns
    'name@version' when anything was refreshed, else None. Only rows still
    flagged seeded=1 and not archived are touched — a name the human took
    over (seeded=0) or shelved is never stomped. A body change lands as a
    normal append-only script_version (update verb, changelog included), so
    any locally diverged body stays recoverable in the version history; a
    description change is an in-place UPDATE (the FTS trigger reindexes)."""
    assert seed.body, "a bundled seed always ships a non-empty body"
    row = conn.execute(
        "SELECT sv.body, sv.version, s.description FROM script s "
        "JOIN script_version sv ON sv.script_id = s.id "
        "WHERE s.name = ? AND s.seeded = 1 AND s.archived = 0 "
        "ORDER BY sv.version DESC LIMIT 1",
        (seed.name,),
    ).fetchone()
    if row is None:
        return None
    version: int = row["version"]
    refreshed = False
    if row["body"] != seed.body:
        request = update.UpdateRequest(name=seed.name, changelog=_SYNC_CHANGELOG, body=seed.body)
        version = update.update_script(conn, request).version
        refreshed = True
    if row["description"] != seed.description:
        conn.execute(
            "UPDATE script SET description = ? WHERE name = ?", (seed.description, seed.name)
        )
        conn.commit()
        refreshed = True
    assert version >= row["version"], "sync never rewinds a version"
    return f"{seed.name}@{version}" if refreshed else None


def load_seeds(conn: sqlite3.Connection) -> list[str]:
    """Writes every seed not already present, flags it seeded=1, and
    re-syncs present-but-drifted seeds with the bundled copies (see
    _sync_seed) so a library always converges on this build's seed set.
    Returns bare names for newly written seeds and 'name@version' for
    re-synced ones; a fully in-sync library returns []. Reuses
    write_script (language gate OFF) so seeds pass the same slug/
    description/lint validation as agent-authored scripts — a bad seed
    body fails loudly here, not silently at first `run`. The gate stays
    off because seeding is a human `grim init` action: the env-derived
    writable set (GRIM_LANGUAGES / GRIM_BASE_LANGUAGES) constrains agent
    writing, never what the stdlib contains — the library floor is
    identical in every environment. Idempotent via explicit name lookup,
    never via exception: the old `except ValueError: continue` also
    swallowed the gate's language rejection, silently dropping every seed
    whenever python wasn't writable.
    """
    applied: list[str] = []
    for seed in SEEDS:
        exists = conn.execute("SELECT 1 FROM script WHERE name = ?", (seed.name,)).fetchone()
        if exists is not None:
            refreshed = _sync_seed(conn, seed)
            if refreshed is not None:
                applied.append(refreshed)
            continue
        request = write.WriteRequest(
            name=seed.name,
            language=seed.language,
            description=seed.description,
            body=seed.body,
            parent=None,
            scope="global",
            session_id=SEED_SESSION_ID,
        )
        write.write_script(conn, request, enforce_language_gate=False)
        conn.execute("UPDATE script SET seeded = 1 WHERE name = ?", (seed.name,))
        conn.commit()
        applied.append(seed.name)

    assert len(applied) <= len(SEEDS), "cannot apply more names than SEEDS declares"
    return applied
