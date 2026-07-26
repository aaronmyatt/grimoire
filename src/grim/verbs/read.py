"""`grim read` — script body+metadata+recent execs, or a paged exec output."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from grim.verbs import _shared

PAGE_LINES = 200


@dataclass(frozen=True)
class ScriptReadResult:
    name: str
    version: int
    language: str
    description: str
    body: str
    recent_executions: list[sqlite3.Row]


def read_script(conn: sqlite3.Connection, name: str, version: int | None) -> ScriptReadResult:
    row = _shared.resolve_script_version(conn, name, version)
    executions = conn.execute(
        "SELECT id, exit_code, duration_ms, started_at FROM execution "
        "WHERE script_version_id = ? ORDER BY started_at DESC LIMIT 3",
        (row["id"],),
    ).fetchall()
    assert row["name"] == name, "resolved row must match the requested name"
    return ScriptReadResult(
        name=row["name"],
        version=row["version"],
        language=row["language"],
        description=row["description"],
        body=row["body"],
        recent_executions=executions,
    )


def read_execution_page(conn: sqlite3.Connection, execution_id: int, page: int) -> str:
    assert page >= 1, "page must be 1-indexed"
    row = conn.execute(
        "SELECT stdout, stderr FROM execution WHERE id = ?", (execution_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"execution #{execution_id} not found")
    lines = ((row["stdout"] or "") + (row["stderr"] or "")).splitlines()
    total_pages = max(1, -(-len(lines) // PAGE_LINES))
    start = (page - 1) * PAGE_LINES
    return f"page {page}/{total_pages}\n" + "\n".join(lines[start : start + PAGE_LINES])


def cmd_read(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        if args.exec is not None:
            print(read_execution_page(conn, args.exec, args.page or 1))
            return 0
        if args.name is None:
            print("error: provide NAME[@V] or --exec ID", file=sys.stderr)
            return 1
        name, version = _shared.parse_name_version(args.name)
        result = read_script(conn, name, version)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{result.name}@{result.version} ({result.language})")
    print(result.description)
    print("---")
    print(result.body)
    for execution in result.recent_executions:
        print(
            f"exec #{execution['id']}: exit {execution['exit_code']} · {execution['duration_ms']}ms"
        )
    return 0
