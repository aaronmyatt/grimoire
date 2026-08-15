"""GrimEnvironment — the hard enforcement point for the six-verb
constraint (build plan D7, Phase 2; D6 revised → native tool-calling).
Subclasses mini-swe-agent's LocalEnvironment but never shells out: the
model's action is a structured grim tool call (GrimToolcallModel),
dispatched in-process via cli.main(). The `submit` tool is the
deterministic task-completion signal — no output sentinel.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Any

from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted
from pydantic import BaseModel

from grim import cli, db
from grim.adapter import trace
from grim.adapter.tools import SUBMIT_TOOL_NAME, tool_call_to_argv

_ARGS_PREVIEW_LIMIT = 200


def _tool_args_snippet(args: dict[str, Any]) -> str:
    """A bounded JSON-ish preview of a tool call's args for the tool.<name>
    span — never the full body/stdin (could be megabytes)."""
    try:
        text = json.dumps(args, default=str)
    except (TypeError, ValueError):
        return ""
    return text if len(text) <= _ARGS_PREVIEW_LIMIT else text[: _ARGS_PREVIEW_LIMIT - 3] + "..."


class GrimEnvironmentConfig(BaseModel):
    session_id: str = ""
    """Stamped on every script/execution row this session creates. Empty
    generates a fresh uuid4 at startup (schema comment: session.id is
    "uuid or 'human-adhoc'")."""

    cwd: str = ""
    """The working directory every action is pinned to. Empty captures the
    launch directory at startup — the same anchor Pi and Claude Code use —
    so a drifted process cwd, or a `cd` inside one script, can never
    relocate later actions. Exported as $GRIM_CWD around each verb call
    (verbs/run.py consumes it and records it on the execution row)."""


class GrimEnvironment(LocalEnvironment):
    def __init__(self, *, config_class: type = GrimEnvironmentConfig, **kwargs: Any) -> None:
        self.config = config_class(**kwargs)
        self.session_id = self.config.session_id or str(uuid.uuid4())
        self.cwd = self.config.cwd or os.getcwd()
        conn = db.connect()  # no `init` verb reaches the agent (D12) — ensure schema exists
        try:
            db.migrate(conn)
        finally:
            conn.close()

    def execute(
        self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None
    ) -> dict[str, Any]:
        """Run one structured grim tool call. GrimToolcallModel produces
        {tool, args} and has already validated the tool name and required
        args. `submit` is the deterministic stop — it raises Submitted with
        the model's result and never scans output for a sentinel. Every
        other tool maps to a single in-process cli.main invocation."""
        assert "tool" in action, "tool-calling only: an action must carry a 'tool'"
        tool = action["tool"]
        args = action.get("args", {})
        with trace.span("tool." + tool, args=_tool_args_snippet(args)):
            # mini rarely passes a cwd; the pin captured at startup is the
            # default so every action shares one stable working directory.
            return self._execute(tool, args, cwd or self.cwd, timeout)
        raise AssertionError("unreachable: _execute always returns or raises")

    def _execute(
        self, tool: str, args: dict[str, Any], cwd: str, timeout: int | None
    ) -> dict[str, Any]:
        """The original execute body, split out so execute() can time the
        whole call in one span (submit raises Submitted; the span records
        error=Submitted and the harness handles it as before)."""
        if tool == SUBMIT_TOOL_NAME:
            result = args.get("result", "")
            raise Submitted(
                {
                    "role": "exit",
                    "content": result,
                    "extra": {"exit_status": "Submitted", "submission": result},
                }
            )
        argv, stdin = tool_call_to_argv(tool, args)
        text, exit_code = _invoke(argv, stdin or "", self.session_id, cwd)
        output = {"output": text, "returncode": exit_code, "exception_info": ""}
        assert "output" in output, "execute() must always return an 'output' key"
        return output


@contextlib.contextmanager
def _session_env(session_id: str, cwd: str) -> Any:
    """Export the session id and the pinned working directory for the span
    of one in-process verb call, then restore. GRIM_SESSION stamps the rows
    the call writes; GRIM_CWD is consumed by verbs/run.py so the dispatched
    script — and, via inherited env, any nested `grim run` it shells out to
    — starts from the pin, never from a drifted process cwd."""
    assert session_id, "a verb call always carries a session id"
    exported = {"GRIM_SESSION": session_id, "GRIM_CWD": cwd}
    previous = {name: os.environ.get(name) for name in exported}
    os.environ.update(exported)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# Several sessions (and the human CLI) share one SQLite library, and WAL
# allows a single writer: a verb's first write can briefly collide with
# another session's commit and raise sqlite3.OperationalError("database is
# locked") after busy_timeout. Contention is transient — every verb write
# transaction is committed promptly — so retry with backoff before giving up.
# A still-locked write degrades into an ordinary nonzero observation the
# agent can read and self-correct from, never a session-killing exception.
_BUSY_RETRIES = 3
_BUSY_BACKOFF_S = (0.5, 1.0, 2.0)


def _invoke(argv: list[str], stdin: str, session_id: str, cwd: str = "") -> tuple[str, int]:
    """Calls cli.main(argv) in-process with captured stdio — the only
    path from the agent into verbs/*, matching D7's "no shell in the
    control plane" (adapter/CLAUDE.md). Requires no changes to cli.py.

    Malformed actions (unknown flag, missing/invalid argument) make
    argparse call ``sys.exit()`` instead of returning — and because this
    runs IN-PROCESS, that ``SystemExit`` would otherwise tear straight
    through the whole agent loop: it's a ``BaseException``, so mini's
    ``run()`` (which only catches ``except Exception``) never sees it and
    the entire session dies mid-turn. Catching it here converts the
    mistake into an ordinary nonzero-returncode observation the agent can
    read (argparse's usage text is already captured on stderr) and
    self-correct from — which is the entire point of the six-verb sandbox.
    Ref: https://docs.python.org/3/library/argparse.html#exiting-methods

    The same never-tear-through-the-loop contract covers every
    sqlite3.Error. A transient OperationalError ("database is locked") is
    retried with short backoff; anything else — e.g. the DataError
    ("string or blob too big") that SQLITE_TOOBIG raises when a run's
    captured output exceeds the connection's value-length limit, which
    killed a 255-step live session on 2026-08-15 — degrades immediately
    into an ordinary observation (returncode 1) the agent can read and
    route around, never a session crash.
    Ref: https://docs.python.org/3/library/sqlite3.html#exceptions
    """
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        attempt = 0
        while True:
            try:
                with (
                    _session_env(session_id, cwd),
                    contextlib.redirect_stdout(stdout_buf),
                    contextlib.redirect_stderr(stderr_buf),
                ):
                    exit_code = cli.main(argv)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= _BUSY_RETRIES:
                    note = (
                        f"[grim] error: database busy — {exc} (another session "
                        "holds the write lock; retry the verb)\n"
                    )
                    return note, 1
                time.sleep(_BUSY_BACKOFF_S[attempt])
                attempt += 1
            except sqlite3.Error as exc:
                # Ordered after OperationalError (its subclass) so only the
                # non-retryable classes land here — DataError, IntegrityError,
                # ProgrammingError. None of them get better on retry.
                note = (
                    f"[grim] error: database rejected the operation — "
                    f"{type(exc).__name__}: {exc} (while running `grim "
                    f"{' '.join(argv[:2])}`; not retryable as-is — if the error "
                    "mentions size, rerun with smaller output/input)\n"
                )
                return note, 1
            except SystemExit as exit_signal:
                # Normalize SystemExit.code to a shell-style int the same way
                # the CPython interpreter does on process exit: None -> 0, an
                # int -> itself, anything else -> 1 (argparse uses 2 for usage
                # errors, 0 for --help). The redirected buffers already hold
                # argparse's message because it writes before calling sys.exit.
                # Ref: https://docs.python.org/3/library/exceptions.html#SystemExit
                code = exit_signal.code
                exit_code = code if isinstance(code, int) else (0 if code is None else 1)
                break
    finally:
        sys.stdin = original_stdin
    assert isinstance(exit_code, int), "invoke must always resolve an int exit code"
    assert sys.stdin is original_stdin, "invoke must restore the caller's stdin"
    return stdout_buf.getvalue() + stderr_buf.getvalue(), exit_code
