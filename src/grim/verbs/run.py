"""`grim run` — dispatch through exec/, record the execution, return the
observation. Full stdout/stderr by default; `--head`/`--tail` opt into
first-N/last-M limiting for huge output. Exit code propagates for humans
(build plan §4).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from grim.exec import dispatch, envelope
from grim.verbs import _shared

DEFAULT_TIMEOUT_S = 120.0
# Hard ceiling on any single `grim run`, mirroring MAX_CALL_DEPTH below: an
# explicit --timeout or $GRIM_TIMEOUT is clamped to this. `grim run` is for
# bounded work; a genuinely long-lived process (a dev server, a watcher)
# belongs in a background job (a `grimbg:`-tagged seed), not a blocking run.
MAX_TIMEOUT_S = 3600.0
_TIMEOUT_ENV = "GRIM_TIMEOUT"


def _timeout_from_env(env_value: str | None) -> float:
    """$GRIM_TIMEOUT as a positive float, else DEFAULT_TIMEOUT_S. External
    input, so a missing/malformed/non-positive value falls back rather than
    asserting (mirrors _current_call_depth's defensive parse below)."""
    if not env_value:
        return DEFAULT_TIMEOUT_S
    try:
        parsed = float(env_value)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    result = parsed if parsed > 0 else DEFAULT_TIMEOUT_S
    assert result > 0, "timeout fallback is always positive"
    return result


def resolve_timeout(explicit: float | None, env_value: str | None) -> float:
    """Resolve the run timeout: explicit --timeout > $GRIM_TIMEOUT > default,
    clamped to (0, MAX_TIMEOUT_S]. Pure so it is unit-testable."""
    chosen = explicit if explicit and explicit > 0 else _timeout_from_env(env_value)
    clamped = min(chosen, MAX_TIMEOUT_S)
    assert clamped > 0, "resolved timeout must be positive"
    assert clamped <= MAX_TIMEOUT_S, "resolved timeout must not exceed the ceiling"
    return clamped


# Composition (build plan §6) lets a script shell out to `grim run` on
# another script, transitively — with no bound, a cyclic or runaway chain
# recurses forever (each call has its own --timeout, but the chain has
# none). Every run exposes GRIM_CALL_DEPTH+1 to the subprocess it dispatches
# (see _child_call_depth), so each nested `grim run` inherits a higher
# count; at this cap the chain is rejected instead of run (build plan §9).
# An explicit max on an otherwise-unbounded recursion, per CLAUDE.md §1.
MAX_CALL_DEPTH = 8
_CALL_DEPTH_ENV = "GRIM_CALL_DEPTH"


class CallDepthExceeded(RuntimeError):
    """A `grim run` composition chain hit MAX_CALL_DEPTH — almost always a
    cycle (A runs B runs A …) rather than a legitimately deep pipeline."""


def _current_call_depth() -> int:
    """How many enclosing `grim run` calls this one is nested inside, read
    from the env the parent's dispatch injected. Absent (a top-level human
    or agent call) or malformed -> 0; this is external input, so it is
    parsed defensively rather than asserted."""
    raw = os.environ.get(_CALL_DEPTH_ENV, "")
    try:
        depth = int(raw)
    except ValueError:
        return 0
    return depth if depth > 0 else 0


def _check_call_depth(script_name: str) -> int:
    """Reject the run if the composition chain is already too deep, else
    return the current depth for the caller to pass to _child_call_depth."""
    depth = _current_call_depth()
    if depth >= MAX_CALL_DEPTH:
        raise CallDepthExceeded(
            f"composition depth limit reached ({depth} >= {MAX_CALL_DEPTH}) running "
            f"'{script_name}': a script chain is calling `grim run` too deeply, "
            "likely a cycle"
        )
    assert 0 <= depth < MAX_CALL_DEPTH, "depth is within bounds past the guard"
    return depth


@contextlib.contextmanager
def _child_call_depth(depth: int) -> Iterator[None]:
    """Expose depth+1 in GRIM_CALL_DEPTH for the duration of a dispatch call
    so the subprocess — and any `grim run` it shells out to — inherits the
    incremented count, then restore the prior value. Mirrors
    adapter/environment.py's _session_env so the in-process adapter path is
    not left with a polluted env between turns."""
    assert depth >= 0, "call depth is non-negative"
    previous = os.environ.get(_CALL_DEPTH_ENV)
    os.environ[_CALL_DEPTH_ENV] = str(depth + 1)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_CALL_DEPTH_ENV, None)
        else:
            os.environ[_CALL_DEPTH_ENV] = previous


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
    depth = _check_call_depth(request.name)
    row = _shared.resolve_script_version(conn, request.name, request.version)
    _shared.ensure_session(conn, request.session_id)
    # Commit now, before the blocking dispatch call below: ensure_session's
    # INSERT OR IGNORE otherwise leaves a write transaction open across the
    # entire (possibly multi-second) subprocess call. If that dispatched
    # script itself shells out to `grim run`/`grim write` (composition —
    # build plan §6), the nested call would contend for this same SQLite
    # write lock and fail with "database is locked".
    conn.commit()

    # depth+1 is exposed to the subprocess so a nested `grim run` inherits
    # it and the chain is bounded by MAX_CALL_DEPTH (build plan §9).
    with _child_call_depth(depth):
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
    # BEGIN IMMEDIATE serializes the claim across PROCESSES that share a
    # session id (several concurrent `grim run`s on "human-adhoc", a bot and
    # an agent stamped with the same GRIM_SESSION): a second writer blocks
    # until this commit, then sees the row and takes the next seq. The window
    # is two statements plus a commit — microseconds — so it does not
    # reintroduce write-lock hold-ups. Rollback on failure releases the lock.
    conn.execute("BEGIN IMMEDIATE")
    try:
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
    except BaseException:
        conn.rollback()
        raise

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


def _resolve_stdin(stdin_file: str | None) -> str | None:
    """The script's stdin, by precedence: --stdin-file wins; otherwise a
    piped/redirected (non-tty) stdin is read eagerly and fed through. That
    second leg is how the adapter's run tool delivers its `stdin` argument —
    environment._invoke parks it in sys.stdin, which a subprocess can never
    see at the fd level, so it was silently dropped and stdin-reading seeds
    hung to the timeout (the 2026-08-12 regression). An interactive tty
    returns None so dispatch leaves the terminal attached for the human.
    Ref: https://docs.python.org/3/library/sys.html#sys.stdin
    """
    if stdin_file:
        return Path(stdin_file).read_text()
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        return sys.stdin.read()
    except ValueError:  # closed stdin (detached daemon) — nothing to feed
        return None


def cmd_run(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        name, version = _shared.parse_name_version(args.name)
        stdin = _resolve_stdin(args.stdin_file)
        timeout = resolve_timeout(args.timeout, os.environ.get(_TIMEOUT_ENV))
        if args.timeout and args.timeout > MAX_TIMEOUT_S:
            print(
                f"[grim] --timeout {args.timeout:g}s exceeds the {MAX_TIMEOUT_S:g}s ceiling; "
                "clamped. Run long-lived processes as a background job instead.",
                file=sys.stderr,
            )
        request = RunRequest(
            name=name,
            version=version,
            argv=args.args,
            stdin=stdin,
            cwd=None,
            timeout=timeout,
            session_id=_shared.session_id_from_env(),
            # getattr keeps this working before cli.py (frozen, committed
            # separately) grows the --head/--tail options; absent → None → full.
            head_lines=getattr(args, "head", None),
            tail_lines=getattr(args, "tail", None),
        )
        try:
            result = run_script(conn, request)
        except (LookupError, CallDepthExceeded) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(result.observation)
        return result.exit_code
    finally:
        conn.close()
