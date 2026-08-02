"""GrimEnvironment — the hard enforcement point for the six-verb
constraint (build plan D7, Phase 2). Subclasses mini-swe-agent's
LocalEnvironment but never shells out: every action is parsed for the
grim six-verb grammar and dispatched in-process via cli.main(), or
answered with a protocol reminder.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import uuid
from typing import Any

from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted
from pydantic import BaseModel

from grim import cli, db
from grim.adapter.parse import parse_grim
from grim.adapter.tools import SUBMIT_TOOL_NAME, tool_call_to_argv

_SUBMIT_SENTINEL = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

PROTOCOL_REMINDER = (
    "Not a grim command. Every response must be exactly one fenced code "
    "block containing a single `grim <verb> ...` invocation, where <verb> "
    "is one of: write, update, read, list, find, run. Raw shell commands "
    'are not executed. Example:\n\n```grim\ngrim find "extract failing tests"\n```'
)


class GrimEnvironmentConfig(BaseModel):
    session_id: str = ""
    """Stamped on every script/execution row this session creates. Empty
    generates a fresh uuid4 at startup (schema comment: session.id is
    "uuid or 'human-adhoc'")."""


class GrimEnvironment(LocalEnvironment):
    def __init__(self, *, config_class: type = GrimEnvironmentConfig, **kwargs: Any) -> None:
        self.config = config_class(**kwargs)
        self.session_id = self.config.session_id or str(uuid.uuid4())
        db.migrate(db.connect())  # no `init` verb reaches the agent (D12) — ensure schema exists

    def execute(
        self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None
    ) -> dict[str, Any]:
        # Native tool-calling path (GrimToolcallModel): the action carries a
        # structured {tool, args} instead of a `command` string to parse.
        if "tool" in action:
            return self._execute_tool(action)
        cmd = parse_grim(action.get("command", ""))
        if cmd is None:
            output: dict[str, Any] = {
                "output": PROTOCOL_REMINDER,
                "returncode": 1,
                "exception_info": "",
            }
        else:
            text, exit_code = _invoke(cmd.argv, cmd.stdin, self.session_id)
            output = {"output": text, "returncode": exit_code, "exception_info": ""}
            # Submission is defined as the OUTPUT OF A RUN (protocol: write a
            # tiny script whose only output is the sentinel, then `grim run`
            # it). Only `run` can finish. Other verbs — notably `grim read`,
            # but also `list`/`find` — merely DISPLAY a script's body or a
            # past run's stored output, which may legitimately contain the
            # sentinel string; checking those falsely finishes the task and,
            # because the agent then re-reads the same script, loops forever.
            if cmd.verb == "run":
                self._check_finished(output)
        assert "output" in output, "execute() must always return an 'output' key"
        return output

    def _execute_tool(self, action: dict[str, Any]) -> dict[str, Any]:
        """Run one structured tool call. `submit` is the deterministic stop
        — it raises Submitted with the model's result and never scans output
        for a sentinel. Every other tool maps to a cli.main invocation."""
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

    def _check_finished(self, output: dict[str, Any]) -> None:
        """Overrides LocalEnvironment's: that version requires the
        sentinel as output's literal first line, but `run`'s observation
        always leads with a "[grim] exec #id..." header (build plan §4),
        so the sentinel is matched as any whole line instead. Only ever
        called for the `run` verb — see execute()."""
        lines = output.get("output", "").splitlines()
        if output.get("returncode") == 0 and any(
            line.strip() == _SUBMIT_SENTINEL for line in lines
        ):
            submission = {
                "role": "exit",
                "content": "",
                "extra": {"exit_status": "Submitted", "submission": ""},
            }
            raise Submitted(submission)


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
    """
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with (
            _session_env(session_id),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            exit_code = cli.main(argv)
    except SystemExit as exit_signal:
        # Normalize SystemExit.code to a shell-style int the same way the
        # CPython interpreter does on process exit: None -> 0, an int ->
        # itself, anything else -> 1 (argparse uses 2 for usage errors, 0
        # for --help). The redirected buffers already hold argparse's
        # message because it writes before calling sys.exit.
        # Ref: https://docs.python.org/3/library/exceptions.html#SystemExit
        code = exit_signal.code
        exit_code = code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        sys.stdin = original_stdin
    assert isinstance(exit_code, int), "invoke must always resolve an int exit code"
    assert sys.stdin is original_stdin, "invoke must restore the caller's stdin"
    return stdout_buf.getvalue() + stderr_buf.getvalue(), exit_code
