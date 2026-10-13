"""`grim find` — FTS5 ranked search: name > description > body (build plan
§4). Usage-prior reranking is Phase 6, measurement-gated — not here.
"""

from __future__ import annotations

import argparse
import sqlite3

from grim.verbs import _shared

DEFAULT_FIND_LIMIT = 5
# One-off scripts (probes, verifications, single-use fixes) take this name
# prefix: still runnable and recorded, but never crowding find's results —
# 35% of the agent's library was such throwaways, reused in another session
# only 16% of the time. A query that itself starts with "scratch" opts back
# in. GLOB, not LIKE: `_` is literal there.
# Ref: https://www.sqlite.org/lang_expr.html#glob
SCRATCH_PREFIX = "scratch_"


def find_scripts(
    conn: sqlite3.Connection, query: str, limit: int = DEFAULT_FIND_LIMIT, scope: str | None = None
) -> list[sqlite3.Row]:
    """FTS hits tiered by provenance before bm25: scripts written in the
    current repo (`scope`) first, deliberately-global scripts second,
    other-repo/legacy scopes last — cross-repo noise never outranks the
    current repo's own library. scope=None (or 'global' when cwd is not a
    repo) leaves only the global/other tiers. `scratch_*` scripts are left
    out unless the query starts with "scratch" (SCRATCH_PREFIX)."""
    assert scope is None or _shared.SCOPE_RE.match(scope), "scope must be resolved before find"
    match_query = _shared.fts_match_query(query)
    if not match_query:
        return []
    include_scratch = query.strip().lower().startswith(SCRATCH_PREFIX.rstrip("_"))
    rows = conn.execute(
        "SELECT s.name, s.description, s.language, "
        "bm25(script_fts, 10.0, 5.0, 1.0) AS rank, "
        "COALESCE(h.runs, 0) AS runs, COALESCE(h.success_rate, 0) AS success_rate, h.last_used "
        "FROM script_fts JOIN script s ON s.id = script_fts.rowid "
        "LEFT JOIN script_health h ON h.id = s.id "
        "WHERE script_fts MATCH ? AND s.archived = 0 AND (? OR s.name NOT GLOB ?) "
        "ORDER BY CASE WHEN s.scope = ? THEN 0 WHEN s.scope = 'global' THEN 1 ELSE 2 END, "
        "rank LIMIT ?",
        (match_query, include_scratch, SCRATCH_PREFIX + "*", scope or "", limit),
    ).fetchall()
    assert len(rows) <= limit, "find_scripts must never exceed the requested limit"
    assert include_scratch or not any(r["name"].startswith(SCRATCH_PREFIX) for r in rows), (
        "scratch scripts surface only when the query asks for them"
    )
    return rows


def cmd_find(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        rows = find_scripts(
            conn, args.query, args.limit or DEFAULT_FIND_LIMIT, scope=_shared.default_scope()
        )
        for row in rows:
            # last_used is NULL until a script's first run; show "-" so the column
            # stays aligned rather than printing the literal "None".
            print(
                f"{row['name']}\t{row['language']}\t{row['description']}"
                f"\truns={row['runs']}\tsuccess={row['success_rate']:.2f}"
                f"\tlast={row['last_used'] or '-'}"
            )
        return 0
    finally:
        conn.close()
