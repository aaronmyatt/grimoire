"""`grim run` — dispatch through exec/, record the execution, return the
observation. Full stdout/stderr by default; `--head`/`--tail` opt into
first-N/last-M limiting for huge output. Exit code propagates for humans
(build plan §4).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from grim.exec import dispatch, envelope
from grim.verbs import _shared

DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class RunRequest:
    name: str
    version: int | None
    argv: list[str]
    stdin: str | None
    cwd: str | None
    timeout: float
    session_id: str
    # None/None means full output; either set collapses the middle of a
    # long stream to its first head_lines and last tail_lines.
    head_lines: int | None = None
    tail_lines: int | None = None


@dataclass(frozen=True)
class RunResult:
    execution_id: int
    exit_code: int
    observation: str


def _next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM execution WHERE session_id = ?", (session_id,)
    ).fetchone()
    seq: int = row["seq"]
    assert seq >= 1, "seq is 1-indexed per session"
    return seq


def run_script(conn: sqlite3.Connection, request: RunRequest) -> RunResult:
    row = _shared.resolve_script_version(conn, request.name, request.version)
    _shared.ensure_session(conn, request.session_id)
    # Commit now, before the blocking dispatch call below: ensure_session's
    # INSERT OR IGNORE otherwise leaves a write transaction open across the
    # entire (possibly multi-second) subprocess call. If that dispatched
    # script itself shells out to `grim run`/`grim write` (composition —
    # build plan §6), the nested call would contend for this same SQLite
    # write lock and fail with "database is locked".
    conn.commit()

    result = dispatch.dispatch(
        dispatch.ScriptVersion(language=row["language"], body=row["body"]),
        dispatch.ExecutionRequest(
            argv=request.argv, stdin=request.stdin, cwd=request.cwd, timeout=request.timeout
        ),
    )

    # Computed after dispatch, not before: a nested `grim run` inside the
    # dispatched script may have already inserted execution rows for this
    # same session, so the "next" seq must be read fresh here to avoid a
    # UNIQUE(session_id, seq) collision with whatever it already claimed.
    seq = _next_seq(conn, request.session_id)
    cursor = conn.execute(
        "INSERT INTO execution (script_version_id, session_id, seq, argv, stdin, cwd, "
        "exit_code, stdout, stderr, duration_ms, env_fingerprint) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            request.session_id,
            seq,
            json.dumps(request.argv),
            request.stdin,
            request.cwd,
            result.exit_code,
            result.stdout,
            result.stderr,
            result.duration_ms,
            result.env_fingerprint,
        ),
    )
    conn.commit()

    assert cursor.lastrowid is not None, "execution insert must produce a rowid"
    header = (
        f"[grim] exec #{cursor.lastrowid} · {row['name']}@{row['version']} · "
        f"exit {result.exit_code} · {result.duration_ms / 1000:.1f}s"
    )
    # Full output by default (head_lines/tail_lines default to None on the
    # request); the caller opts into first-N/last-M limiting via --head/--tail.
    body = envelope.truncate(
        result.stdout, result.stderr, head_lines=request.head_lines, tail_lines=request.tail_lines
    )
    return RunResult(
        execution_id=cursor.lastrowid, exit_code=result.exit_code, observation=f"{header}\n{body}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    name, version = _shared.parse_name_version(args.name)
    stdin = Path(args.stdin_file).read_text() if args.stdin_file else None
    request = RunRequest(
        name=name,
        version=version,
        argv=args.args,
        stdin=stdin,
        cwd=None,
        timeout=args.timeout or DEFAULT_TIMEOUT_S,
        session_id=_shared.session_id_from_env(),
        # getattr keeps this working before cli.py (frozen, committed
        # separately) grows the --head/--tail options; absent → None → full.
        head_lines=getattr(args, "head", None),
        tail_lines=getattr(args, "tail", None),
    )
    try:
        result = run_script(conn, request)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.observation)
    return result.exit_code
