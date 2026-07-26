"""`grim list` — terse, paginated rows. Full bodies stay behind `read`."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

from grim.verbs import _shared

DEFAULT_LIST_LIMIT = 20


@dataclass(frozen=True)
class ListFilters:
    scope: str | None
    language: str | None
    limit: int
    offset: int


def list_scripts(conn: sqlite3.Connection, filters: ListFilters) -> list[sqlite3.Row]:
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
    query += " ORDER BY s.name LIMIT ? OFFSET ?"
    params.extend([filters.limit, filters.offset])

    rows = conn.execute(query, params).fetchall()
    assert len(rows) <= filters.limit, "list_scripts must never exceed the requested limit"
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    filters = ListFilters(
        scope=args.scope,
        language=args.lang,
        limit=args.limit or DEFAULT_LIST_LIMIT,
        offset=args.offset or 0,
    )
    for row in list_scripts(conn, filters):
        print(f"{row['name']}\t{row['language']}\t{row['scope']}\t{row['description']}")
    return 0
