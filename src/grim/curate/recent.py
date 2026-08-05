"""`grim recent` — the library by last-run time, newest first, read from the
`script_health` view (migration 0001). "What did I run lately" as a first-
class view instead of `grim list --sort recent` plus a squint.
"""

from __future__ import annotations

import argparse
import sqlite3

from grim.curate import _shared

DEFAULT_RECENT_LIMIT = 10

# script_health has no archived column; join script to drop archived rows, and
# WHERE last_used IS NOT NULL excludes never-run scripts (this view is only
# about recency of actual runs).
_RECENT_SQL = (
    "SELECT h.name, h.runs, h.last_used FROM script_health h "
    "JOIN script s ON s.id = h.id "
    "WHERE h.last_used IS NOT NULL AND s.archived = 0 "
    "ORDER BY h.last_used DESC LIMIT ?"
)


def recent_scripts(
    conn: sqlite3.Connection, limit: int = DEFAULT_RECENT_LIMIT
) -> list[sqlite3.Row]:
    assert limit > 0, "recent limit must be positive"
    rows = conn.execute(_RECENT_SQL, (limit,)).fetchall()
    assert len(rows) <= limit, "recent_scripts must not exceed the requested limit"
    return rows


def cmd_recent(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        for row in recent_scripts(conn, args.limit or DEFAULT_RECENT_LIMIT):
            print(f"{row['name']}\truns={row['runs']}\tlast={row['last_used']}")
        return 0
    finally:
        conn.close()
