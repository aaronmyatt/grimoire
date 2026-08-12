"""Seeds the library on `grim init` (build plan §3 Phase 3). The only
entry point anything outside this slice calls (seeds/CLAUDE.md).
"""

from __future__ import annotations

import sqlite3

from grim.seeds.bodies import SEEDS
from grim.verbs import write

SEED_SESSION_ID = "human-adhoc"  # grim init is a human action


def load_seeds(conn: sqlite3.Connection) -> list[str]:
    """Writes every seed not already present, flags it seeded=1. Reuses
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
    newly_seeded: list[str] = []
    for seed in SEEDS:
        exists = conn.execute("SELECT 1 FROM script WHERE name = ?", (seed.name,)).fetchone()
        if exists is not None:
            continue  # already seeded — re-running `grim init` is a no-op
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
        newly_seeded.append(seed.name)

    assert len(newly_seeded) <= len(SEEDS), "cannot seed more names than SEEDS declares"
    return newly_seeded
