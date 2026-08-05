"""Smoke tests for exec/dispatch.py — bash + python runners only (Phase 1
scope; build plan Phase 4 extends the table). No network access needed:
a bare python script with no PEP 723 header runs fine under `uv run`.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from grim.exec.dispatch import (
    SUPPORTED_LANGUAGES,
    TIMEOUT_EXIT_CODE,
    ExecutionRequest,
    ExecutionResult,
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


def test_bash_binary_output_decodes_with_replacement_not_crash() -> None:
    # Regression: a script emitting non-UTF-8 bytes (e.g. cat-ing a binary
    # file) used to raise UnicodeDecodeError inside proc.communicate(),
    # killing the whole caller. errors="replace" maps undecodable bytes to
    # U+FFFD instead. Ref: https://docs.python.org/3/library/codecs.html#error-handlers
    sv = ScriptVersion(language="bash", body=r"printf '\xa2binary\xff'")
    result = dispatch(sv, _request())
    assert result.exit_code == 0
    assert result.stdout == "�binary�"


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


def test_python_runner_never_attaches_to_grimoires_own_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A python script must run isolated from grimoire's own pyproject.toml/
    .venv (which `uv run` would otherwise attach to whenever cwd resolves
    inside this repo) — regression test for the --no-project flag. Patches the
    _run subprocess layer so it's a pure argv check with no real execution."""
    captured: dict[str, list[str]] = {}

    def fake_run(
        command: list[str], stdin: str | None, cwd: str | None, timeout: float
    ) -> ExecutionResult:
        captured["command"] = command
        return ExecutionResult(exit_code=0, stdout="", stderr="", duration_ms=0, env_fingerprint="")

    monkeypatch.setattr("grim.exec.dispatch._run", fake_run)
    dispatch(ScriptVersion(language="python", body="pass"), _request(timeout=PYTHON_TIMEOUT_S))

    assert "--no-project" in captured["command"]


# Distinctive sleep durations so pgrep can't collide with unrelated processes.
_ORPHAN_MARKER_TIMEOUT = "sleep 88881"
_ORPHAN_MARKER_KILLGRP = "sleep 88882"


def _running(pattern: str) -> bool:
    return bool(
        subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.strip()
    )


def _poll(pattern: str, *, want: bool, tries: int = 40, delay: float = 0.05) -> bool:
    """Poll pgrep until the pattern's presence matches `want` (bounded ~2s) —
    a deterministic wait for async process start/kill, not a blind sleep."""
    for _ in range(tries):
        if _running(pattern) == want:
            return True
        time.sleep(delay)
    return False


def test_timeout_reaps_grandchildren_not_just_direct_child() -> None:
    # bash forks `sleep`, so the sleeper is a grandchild of the python process.
    # The old subprocess.run path SIGKILLed only bash, orphaning that sleep;
    # killing the whole process group must take the grandchild down too.
    sv = ScriptVersion(language="bash", body=_ORPHAN_MARKER_TIMEOUT)
    try:
        result = dispatch(sv, _request(timeout=SLOW_SCRIPT_TIMEOUT_S))
        assert result.exit_code == TIMEOUT_EXIT_CODE
        assert _poll(_ORPHAN_MARKER_TIMEOUT, want=False), "timeout orphaned the grandchild"
    finally:
        subprocess.run(["pkill", "-f", _ORPHAN_MARKER_TIMEOUT], capture_output=True)


def test_kill_group_terminates_a_grandchild() -> None:
    from grim.exec.dispatch import _kill_group

    proc = subprocess.Popen(
        ["bash", "-c", _ORPHAN_MARKER_KILLGRP],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert _poll(_ORPHAN_MARKER_KILLGRP, want=True), "grandchild should be up before kill"
        _kill_group(proc)
        assert _poll(_ORPHAN_MARKER_KILLGRP, want=False), "_kill_group left an orphan"
    finally:
        subprocess.run(["pkill", "-f", _ORPHAN_MARKER_KILLGRP], capture_output=True)
