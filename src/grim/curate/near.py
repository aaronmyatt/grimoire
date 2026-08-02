"""`grim near` — scripts that tend to run adjacently to NAME, read from the
`script_affinity` view. That view is emergent (built from execution `seq`
adjacency within a session, migration 0001), so "runs before/after" is
observed fact, never authored wiring — the human-facing read of the same
signal the gardener/ranking use.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from grim.curate import _shared

DEFAULT_NEAR_LIMIT = 5

# script_affinity(a, b, times_adjacent): script `a` ran immediately before
# `b` in some session. Precedes NAME -> rows where b = NAME's id; follows NAME
# -> rows where a = NAME's id. a != b drops self-adjacency (a script rerun
# back-to-back), which is noise here, not a neighbour.
_PRECEDES_SQL = (
    "SELECT s.name, af.times_adjacent FROM script_affinity af "
    "JOIN script s ON s.id = af.a "
    "WHERE af.b = ? AND af.a != af.b ORDER BY af.times_adjacent DESC, s.name LIMIT ?"
)
_FOLLOWS_SQL = (
    "SELECT s.name, af.times_adjacent FROM script_affinity af "
    "JOIN script s ON s.id = af.b "
    "WHERE af.a = ? AND af.a != af.b ORDER BY af.times_adjacent DESC, s.name LIMIT ?"
)


def _script_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM script WHERE name = ? AND archived = 0", (name,)).fetchone()
    return int(row["id"]) if row is not None else None


def neighbors(
    conn: sqlite3.Connection, name: str, limit: int = DEFAULT_NEAR_LIMIT
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """(precedes, follows) neighbour rows for NAME. Raises LookupError if NAME
    is unknown/archived — external input, not an assert (root CLAUDE.md §3)."""
    assert limit > 0, "near limit must be positive"
    script_id = _script_id(conn, name)
    if script_id is None:
        raise LookupError(f"script {name!r} not found")
    precedes = conn.execute(_PRECEDES_SQL, (script_id, limit)).fetchall()
    follows = conn.execute(_FOLLOWS_SQL, (script_id, limit)).fetchall()
    assert len(precedes) <= limit and len(follows) <= limit, "neighbors must not exceed limit"
    return precedes, follows


def cmd_near(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        precedes, follows = neighbors(conn, args.name, args.limit or DEFAULT_NEAR_LIMIT)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for label, rows in (("before", precedes), ("after", follows)):
        for row in rows:
            print(f"{label}\t{row['name']}\ttimes={row['times_adjacent']}")
    return 0
