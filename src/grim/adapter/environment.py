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
from grim.adapter.tools import SUBMIT_TOOL_NAME, tool_call_to_argv


class GrimEnvironmentConfig(BaseModel):
    session_id: str = ""
    """Stamped on every script/execution row this session creates. Empty
    generates a fresh uuid4 at startup (schema comment: session.id is
    "uuid or 'human-adhoc'")."""


class GrimEnvironment(LocalEnvironment):
    def __init__(self, *, config_class: type = GrimEnvironmentConfig, **kwargs: Any) -> None:
        self.config = config_class(**kwargs)
        self.session_id = self.config.session_id or str(uuid.uuid4())
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
        text, exit_code = _invoke(argv, stdin or "", self.session_id)
        output = {"output": text, "returncode": exit_code, "exception_info": ""}
        assert "output" in output, "execute() must always return an 'output' key"
        return output


@contextlib.contextmanager
def _session_env(session_id: str) -> Any:
    previous = os.environ.get("GRIM_SESSION")
    os.environ["GRIM_SESSION"] = session_id
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GRIM_SESSION", None)
        else:
            os.environ["GRIM_SESSION"] = previous


# Several sessions (and the human CLI) share one SQLite library, and WAL
# allows a single writer: a verb's first write can briefly collide with
# another session's commit and raise sqlite3.OperationalError("database is
# locked") after busy_timeout. Contention is transient — every verb write
# transaction is committed promptly — so retry with backoff before giving up.
# A still-locked write degrades into an ordinary nonzero observation the
# agent can read and self-correct from, never a session-killing exception.
_BUSY_RETRIES = 3
_BUSY_BACKOFF_S = (0.5, 1.0, 2.0)


def _invoke(argv: list[str], stdin: str, session_id: str) -> tuple[str, int]:
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

    The same never-tear-through-the-loop contract covers a transient
    sqlite3.OperationalError ("database is locked"): retry cli.main with
    short backoff; only a persistently locked write becomes an ordinary
    observation (returncode 1) instead of crashing the session.
    """
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        attempt = 0
        while True:
            try:
                with (
                    _session_env(session_id),
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
