"""Internal helpers for the curate slice — not public surface.

Slices never import each other (root CLAUDE.md §2), so this connect() is a
deliberate copy of verbs/_shared.connect(), not a shared-kernel promotion —
it's three trivial lines and the duplication buys slice independence.
"""

from __future__ import annotations

import sqlite3

from grim import db


def connect() -> sqlite3.Connection:
    """`db.connect()` with row_factory set, so curate SQL can use `row["col"]`."""
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    return conn
