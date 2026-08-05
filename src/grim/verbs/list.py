"""`grim list` — terse, paginated rows. Full bodies stay behind `read`."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

from grim.verbs import _shared

DEFAULT_LIST_LIMIT = 20

# Whitelisted ORDER BY clauses, keyed by the --sort choice. Interpolated into
# the query string, so this dict is the ONLY sort text that ever reaches SQL —
# never a raw arg value (SQLite has no bind params for ORDER BY identifiers).
# `recent` puts NULL last_used (never-run scripts) last via the `col IS NULL`
# idiom. Ref: https://www.sqlite.org/lang_select.html#the_order_by_clause
_SORT_CLAUSES = {
    "name": "s.name",
    "recent": "h.last_used IS NULL, h.last_used DESC",
    "runs": "h.runs DESC, s.name",
}
DEFAULT_SORT = "name"


@dataclass(frozen=True)
class ListFilters:
    scope: str | None
    language: str | None
    limit: int
    offset: int
    sort: str = DEFAULT_SORT


def list_scripts(conn: sqlite3.Connection, filters: ListFilters) -> list[sqlite3.Row]:
    assert filters.sort in _SORT_CLAUSES, f"unknown sort {filters.sort!r}"
    query = (
        "SELECT s.name, s.language, s.scope, s.description, "
        "COALESCE(h.runs, 0) AS runs, COALESCE(h.success_rate, 0) AS success_rate "
        "FROM script s LEFT JOIN script_health h ON h.id = s.id WHERE s.archived = 0"
    )
    params: list[object] = []
    if filters.scope is not None:
        query += " AND s.scope = ?"
        params.append(filters.scope)
    if filters.language is not None:
        query += " AND s.language = ?"
        params.append(filters.language)
    query += f" ORDER BY {_SORT_CLAUSES[filters.sort]} LIMIT ? OFFSET ?"
    params.extend([filters.limit, filters.offset])

    rows = conn.execute(query, params).fetchall()
    assert len(rows) <= filters.limit, "list_scripts must never exceed the requested limit"
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        filters = ListFilters(
            scope=args.scope,
            language=args.lang,
            limit=args.limit or DEFAULT_LIST_LIMIT,
            offset=args.offset or 0,
            # getattr default bridges callers whose Namespace predates the --sort
            # flag (the frozen cli.py wiring lands as its own commit); real CLI use
            # always sets it via argparse choices=/default="name".
            sort=getattr(args, "sort", None) or DEFAULT_SORT,
        )
        for row in list_scripts(conn, filters):
            print(f"{row['name']}\t{row['language']}\t{row['scope']}\t{row['description']}")
        return 0
    finally:
        conn.close()
