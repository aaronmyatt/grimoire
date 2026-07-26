"""`grim update` — append a new script_version. Append-only (D4): never
mutates an existing version.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from grim.verbs import _shared


@dataclass(frozen=True)
class UpdateRequest:
    name: str
    changelog: str
    body: str


@dataclass(frozen=True)
class UpdateResult:
    script_id: int
    version_id: int
    version: int


def update_script(conn: sqlite3.Connection, request: UpdateRequest) -> UpdateResult:
    if not request.changelog.strip():
        raise ValueError("changelog is required")
    latest = _shared.resolve_script_version(conn, request.name, None)
    lint_error = _shared.lint(latest["language"], request.body)
    if lint_error:
        raise ValueError(lint_error)

    new_version = latest["version"] + 1
    cursor = conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash, changelog) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            latest["script_id"],
            new_version,
            request.body,
            _shared.body_hash(request.body),
            request.changelog,
        ),
    )
    conn.commit()

    assert cursor.lastrowid is not None, "script_version insert must produce a rowid"
    assert new_version > latest["version"], "update must always increment the version"
    return UpdateResult(
        script_id=latest["script_id"], version_id=cursor.lastrowid, version=new_version
    )


def cmd_update(args: argparse.Namespace) -> int:
    body = sys.stdin.read()
    conn = _shared.connect()
    request = UpdateRequest(name=args.name, changelog=args.changelog, body=body)
    try:
        result = update_script(conn, request)
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"updated {args.name}@{result.version}")
    return 0
