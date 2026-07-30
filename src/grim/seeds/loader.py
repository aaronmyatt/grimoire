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
    write_script so seeds pass the same slug/description/lint validation
    as agent-authored scripts — a bad seed body fails loudly here, not
    silently at first `run`. Idempotent: an existing name is skipped,
    never duplicated or errored on (seeds/CLAUDE.md invariant).
    """
    newly_seeded: list[str] = []
    for seed in SEEDS:
        request = write.WriteRequest(
            name=seed.name,
            language=seed.language,
            description=seed.description,
            body=seed.body,
            parent=None,
            scope="global",
            session_id=SEED_SESSION_ID,
        )
        try:
            write.write_script(conn, request)
        except ValueError:
            continue  # already seeded
        conn.execute("UPDATE script SET seeded = 1 WHERE name = ?", (seed.name,))
        conn.commit()
        newly_seeded.append(seed.name)

    assert len(newly_seeded) <= len(SEEDS), "cannot seed more names than SEEDS declares"
    return newly_seeded
