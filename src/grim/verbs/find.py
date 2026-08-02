"""`grim find` — FTS5 ranked search: name > description > body (build plan
§4). Usage-prior reranking is Phase 6, measurement-gated — not here.
"""

from __future__ import annotations

import argparse
import sqlite3

from grim.verbs import _shared

DEFAULT_FIND_LIMIT = 5


def find_scripts(
    conn: sqlite3.Connection, query: str, limit: int = DEFAULT_FIND_LIMIT
) -> list[sqlite3.Row]:
    match_query = _shared.fts_match_query(query)
    if not match_query:
        return []
    rows = conn.execute(
        "SELECT s.name, s.description, s.language, "
        "bm25(script_fts, 10.0, 5.0, 1.0) AS rank, "
        "COALESCE(h.runs, 0) AS runs, COALESCE(h.success_rate, 0) AS success_rate, h.last_used "
        "FROM script_fts JOIN script s ON s.id = script_fts.rowid "
        "LEFT JOIN script_health h ON h.id = s.id "
        "WHERE script_fts MATCH ? AND s.archived = 0 ORDER BY rank LIMIT ?",
        (match_query, limit),
    ).fetchall()
    assert len(rows) <= limit, "find_scripts must never exceed the requested limit"
    return rows


def cmd_find(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    rows = find_scripts(conn, args.query, args.limit or DEFAULT_FIND_LIMIT)
    for row in rows:
        # last_used is NULL until a script's first run; show "-" so the column
        # stays aligned rather than printing the literal "None".
        print(
            f"{row['name']}\t{row['language']}\t{row['description']}"
            f"\truns={row['runs']}\tsuccess={row['success_rate']:.2f}"
            f"\tlast={row['last_used'] or '-'}"
        )
    return 0
