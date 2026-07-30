"""`grim run` — dispatch through exec/, record the execution, return a
truncated observation. Exit code propagates for humans (build plan §4).
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

# The shell escape hatch (build plan §3/§7) gets tighter truncation than
# named scripts so the observed token cost visibly favors promoting a
# repeated `shell` invocation to a named script (envelope.truncate's
# defaults — 40/10 — apply to everything else).
SHELL_SCRIPT_NAME = "shell"
SHELL_HEAD_LINES = 10
SHELL_TAIL_LINES = 3


@dataclass(frozen=True)
class RunRequest:
    name: str
    version: int | None
    argv: list[str]
    stdin: str | None
    cwd: str | None
    timeout: float
    session_id: str


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
    seq = _next_seq(conn, request.session_id)

    result = dispatch.dispatch(
        dispatch.ScriptVersion(language=row["language"], body=row["body"]),
        dispatch.ExecutionRequest(
            argv=request.argv, stdin=request.stdin, cwd=request.cwd, timeout=request.timeout
        ),
    )

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
    if row["name"] == SHELL_SCRIPT_NAME:
        body = envelope.truncate(
            result.stdout, result.stderr, head_lines=SHELL_HEAD_LINES, tail_lines=SHELL_TAIL_LINES
        )
    else:
        body = envelope.truncate(result.stdout, result.stderr)
    return RunResult(
        execution_id=cursor.lastrowid, exit_code=result.exit_code, observation=f"{header}\n{body}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    name, version = _shared.parse_name_version(args.name)
    stdin = Path(args.stdin_file).read_text() if args.stdin_file else None
    argv = args.args[1:] if args.args and args.args[0] == "--" else (args.args or [])
    request = RunRequest(
        name=name,
        version=version,
        argv=argv,
        stdin=stdin,
        cwd=None,
        timeout=args.timeout or DEFAULT_TIMEOUT_S,
        session_id=_shared.session_id_from_env(),
    )
    try:
        result = run_script(conn, request)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.observation)
    return result.exit_code
