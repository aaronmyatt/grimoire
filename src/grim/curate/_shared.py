"""Internal helpers for the curate slice — not public surface.

Slices never import each other (root CLAUDE.md §2), so connect()/
resolve_script_version()/lint()/body_hash() are deliberate copies of
verbs/_shared.py's — not a shared-kernel promotion. The duplication buys
slice independence (curate must never import verbs/, curate/CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess

from grim import db


def connect() -> sqlite3.Connection:
    """`db.connect()` with row_factory set, so curate SQL can use `row["col"]`."""
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    return conn


def resolve_script_version(conn: sqlite3.Connection, name: str, version: int | None) -> sqlite3.Row:
    """Fetch script_version joined with script; latest when version is
    None. Raises LookupError if missing — external input, not an assert."""
    query = (
        "SELECT sv.id, sv.script_id, sv.version, sv.body, sv.body_hash, "
        "s.name, s.language, s.description "
        "FROM script_version sv JOIN script s ON s.id = sv.script_id WHERE s.name = ?"
    )
    params: list[object] = [name]
    if version is None:
        query += " ORDER BY sv.version DESC LIMIT 1"
    else:
        query += " AND sv.version = ?"
        params.append(version)
    row = conn.execute(query, params).fetchone()
    if row is None:
        spec = name if version is None else f"{name}@{version}"
        raise LookupError(f"script {spec!r} not found")
    assert isinstance(row, sqlite3.Row), "connect() must set row_factory = sqlite3.Row"
    assert row["name"] == name, "resolved row must match the requested script name"
    return row


def lint(language: str, body: str) -> str | None:
    """Diagnostic string if `body` fails syntax lint, else None (unknown
    languages pass silently — Phase 1 scope is bash + python)."""
    if language == "python":
        try:
            compile(body, "<grim edit>", "exec")
        except SyntaxError as exc:
            return f"python syntax error: {exc}"
        return None
    if language == "bash":
        result = subprocess.run(
            ["bash", "-n"], input=body, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return f"bash syntax error: {result.stderr.strip()}"
        return None
    return None


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()
