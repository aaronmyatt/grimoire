"""Language dispatch table and subprocess execution.

The only entry point `verbs/run.py` calls (see exec/CLAUDE.md). Stateless:
every call is a pure function of its arguments; nothing persists.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_EXIT_CODE = 124
# After killing a job's process group, how long to wait for its pipes to close
# while draining output before falling back to a direct kill(). SIGKILL makes
# this near-instant; the bound only guards a pathological unkillable descendant.
_REAP_TIMEOUT_S = 5.0


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
#
# python's --no-project (https://docs.astral.sh/uv/reference/cli/#uv-run)
# stops `uv run` from ever attaching to *grimoire's own* pyproject.toml/
# .venv, which it would otherwise do by default whenever the dispatched
# script's cwd resolves inside this repo. A script that declares its own
# dependencies via PEP 723 inline metadata (a `# /// script` header) still
# gets an isolated, uv-cached venv resolved from that header alone — this
# is the sandboxed "install a dependency" story for python scripts, not a
# regression in it. A bare script with no header just runs against uv's
# base interpreter, dependency-free.
_RUNNERS: dict[str, tuple[str, Callable[[Path], list[str]], list[str]]] = {
    "bash": (".sh", lambda path: ["bash", str(path)], ["bash", "--version"]),
    "python": (
        ".py",
        lambda path: ["uv", "run", "--no-project", str(path)],
        ["uv", "--version"],
    ),
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


def _kill_group(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """SIGKILL the child's whole process group, then drain its output. Because
    _run starts the child with start_new_session, the child leads its own
    group, so this reaps grandchildren too — a `uv run` python, a bash-spawned
    `sleep` — that proc.kill() (which signals only the direct child) would
    orphan. Ref: https://docs.python.org/3/library/os.html#os.killpg
    """
    assert proc.pid is not None, "a started Popen always has a pid"
    # getpgid/killpg race the child's own exit; a dead group is not an error.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        stdout, stderr = proc.communicate(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    assert proc.returncode is not None, "process is reaped after kill"
    return stdout or "", stderr or ""


def _run(command: list[str], stdin: str | None, cwd: str | None, timeout: float) -> ExecutionResult:
    """Run `command` in its OWN process group (start_new_session) so a timeout
    or a Ctrl-C can take down the whole job tree. subprocess.run only SIGKILLs
    the direct child on timeout/interrupt, orphaning grandchildren; killing the
    group (see _kill_group) does not. Timeout -> exit 124. KeyboardInterrupt ->
    kill the group, then re-raise so the harness's interrupt handler still runs
    (two Ctrl-C: the first cancels the run here, the second exits upstream).
    Ref: https://docs.python.org/3/library/subprocess.html#subprocess.Popen
    """
    assert timeout > 0, "timeout must be positive"
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr = _kill_group(proc)
        exit_code = TIMEOUT_EXIT_CODE
    except KeyboardInterrupt:
        _kill_group(proc)
        raise
    duration_ms = int((time.monotonic() - started) * 1000)
    assert exit_code is not None, "a resolved run always has an exit code"
    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        env_fingerprint="",
    )
