"""Language dispatch table and subprocess execution.

The only entry point `verbs/run.py` calls (see exec/CLAUDE.md). Stateless:
every call is a pure function of its arguments; nothing persists.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_EXIT_CODE = 124


@dataclass(frozen=True)
class ScriptVersion:
    language: str
    body: str


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    env_fingerprint: str


@dataclass(frozen=True)
class ExecutionRequest:
    argv: list[str]
    stdin: str | None
    cwd: str | None
    timeout: float


# language -> (file suffix, argv-prefix builder, version-check argv)
# Phase 1 scope: bash + python only; Phase 4 extends this table with
# js/ts -> bun and a `run --json` fallback for everything else (D9).
_RUNNERS: dict[str, tuple[str, Callable[[Path], list[str]], list[str]]] = {
    "bash": (".sh", lambda path: ["bash", str(path)], ["bash", "--version"]),
    "python": (".py", lambda path: ["uv", "run", str(path)], ["uv", "--version"]),
}

# Public so verbs/write.py can reject unsupported languages at write
# time instead of only failing later here at run time.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(_RUNNERS)


def _env_fingerprint(version_argv: list[str]) -> str:
    """`<tool> --version`, first line only — cheap and always available.

    Not persisted here (stateless invariant); the caller is responsible for
    storing it on the execution row so Phase 4's gardener can use it for
    staleness triage.
    """
    result = subprocess.run(version_argv, capture_output=True, text=True, check=False)
    output = result.stdout or result.stderr
    first_line = output.splitlines()[0] if output else ""
    return f"{version_argv[0]}:{first_line}"


def dispatch(script_version: ScriptVersion, request: ExecutionRequest) -> ExecutionResult:
    """Run `script_version.body` under the runner for its language.

    Raises ValueError for a language outside `_RUNNERS` — `language` is
    external input (came from `grim write --lang`), so this is real
    validation, not an assertion (root CLAUDE.md §3).
    """
    assert request.timeout > 0, "timeout must be positive"
    if script_version.language not in _RUNNERS:
        supported = ", ".join(sorted(_RUNNERS))
        raise ValueError(
            f"unsupported language {script_version.language!r} — "
            f"supported: {supported} (Phase 4 extends this table)"
        )
    suffix, build_argv, version_argv = _RUNNERS[script_version.language]
    fingerprint = _env_fingerprint(version_argv)

    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=True) as f:
        f.write(script_version.body)
        f.flush()
        command = build_argv(Path(f.name)) + request.argv
        result = _run(command, request.stdin, request.cwd, request.timeout)

    assert result.exit_code is not None, "dispatch must always resolve an exit code"
    return ExecutionResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        env_fingerprint=fingerprint,
    )


def _run(command: list[str], stdin: str | None, cwd: str | None, timeout: float) -> ExecutionResult:
    """Turns a timeout into exit 124 instead of propagating TimeoutExpired —
    subprocess.run() already kills the process for us on timeout.
    Ref: https://docs.python.org/3/library/subprocess.html#subprocess.run
    """
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = TIMEOUT_EXIT_CODE
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
    duration_ms = int((time.monotonic() - started) * 1000)
    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        env_fingerprint="",
    )
