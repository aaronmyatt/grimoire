"""Smoke tests for exec/dispatch.py — bash + python runners only (Phase 1
scope; build plan Phase 4 extends the table). No network access needed:
a bare python script with no PEP 723 header runs fine under `uv run`.
"""

from __future__ import annotations

import time

import pytest

from grim.exec.dispatch import (
    SUPPORTED_LANGUAGES,
    TIMEOUT_EXIT_CODE,
    ExecutionRequest,
    ScriptVersion,
    dispatch,
)

BASH_TIMEOUT_S = 5
PYTHON_TIMEOUT_S = 30
SLOW_SCRIPT_TIMEOUT_S = 0.2
TIMEOUT_KILL_BOUND_S = 5
NONZERO_EXIT = 3


def _request(
    argv: list[str] | None = None,
    stdin: str | None = None,
    cwd: str | None = None,
    timeout: float = BASH_TIMEOUT_S,
) -> ExecutionRequest:
    return ExecutionRequest(argv=argv or [], stdin=stdin, cwd=cwd, timeout=timeout)


def test_bash_captures_stdout_and_exit_code() -> None:
    sv = ScriptVersion(language="bash", body="echo hello")
    result = dispatch(sv, _request())
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_bash_propagates_nonzero_exit() -> None:
    sv = ScriptVersion(language="bash", body=f"exit {NONZERO_EXIT}")
    result = dispatch(sv, _request())
    assert result.exit_code == NONZERO_EXIT


def test_bash_receives_argv() -> None:
    sv = ScriptVersion(language="bash", body='echo "$1-$2"')
    result = dispatch(sv, _request(argv=["a", "b"]))
    assert result.stdout.strip() == "a-b"


def test_bash_receives_stdin() -> None:
    sv = ScriptVersion(language="bash", body="cat")
    result = dispatch(sv, _request(stdin="piped in"))
    assert result.stdout == "piped in"


def test_bash_respects_cwd(tmp_path: object) -> None:
    sv = ScriptVersion(language="bash", body="pwd")
    result = dispatch(sv, _request(cwd=str(tmp_path)))
    assert result.stdout.strip() == str(tmp_path)


def test_bash_timeout_kills_process_and_reports_124() -> None:
    sv = ScriptVersion(language="bash", body="sleep 10")
    started = time.monotonic()
    result = dispatch(sv, _request(timeout=SLOW_SCRIPT_TIMEOUT_S))
    elapsed = time.monotonic() - started
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert elapsed < TIMEOUT_KILL_BOUND_S, (
        "timeout must actually kill the subprocess, not wait it out"
    )


def test_python_captures_stdout() -> None:
    sv = ScriptVersion(language="python", body="print('hi')")
    result = dispatch(sv, _request(timeout=PYTHON_TIMEOUT_S))
    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0


def test_python_propagates_nonzero_exit() -> None:
    sv = ScriptVersion(language="python", body="import sys; sys.exit(1)")
    result = dispatch(sv, _request(timeout=PYTHON_TIMEOUT_S))
    assert result.exit_code == 1


def test_unsupported_language_raises_value_error() -> None:
    sv = ScriptVersion(language="ruby", body="puts 'hi'")
    with pytest.raises(ValueError, match="unsupported language"):
        dispatch(sv, _request())


def test_env_fingerprint_present_for_bash_and_python() -> None:
    bash_result = dispatch(ScriptVersion(language="bash", body="true"), _request())
    python_result = dispatch(
        ScriptVersion(language="python", body="pass"), _request(timeout=PYTHON_TIMEOUT_S)
    )
    assert bash_result.env_fingerprint.startswith("bash:")
    assert python_result.env_fingerprint.startswith("uv:")


def test_supported_languages_is_bash_and_python_only() -> None:
    # Documents the exact contract verbs/write.py validates against —
    # catches accidental drift when Phase 4 extends the dispatch table.
    assert SUPPORTED_LANGUAGES == frozenset({"bash", "python"})
