#!/usr/bin/env python3
"""Language dispatch table and subprocess execution.

The only entry point `verbs/run.py` calls (see exec/CLAUDE.md). Stateless:
every call is a pure function of its arguments; nothing persists.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
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
# Harness-internal system calls are always fast; exceeding these bounds means
# the platform is unhealthy, and that must fail loudly, never be absorbed
# into a script's wall time (TigerStyle: assert the negative space).
_SPAWN_BUDGET_MS = 1_000
_VERSION_PROBE_TIMEOUT_S = 5.0

# Env var carrying the extended languages the user enabled (comma-joined).
# config.py seeds it from ~/.grimoire/config.toml's `[languages]` table; a
# shell-set value wins (setdefault). Unset/empty = everything off, the default.
LANGUAGES_ENV = "GRIM_LANGUAGES"

# Env var SUBSETTING which builtin languages the agent may write: unset ->
# both python and bash (status quo); a set value names the subset to keep,
# and '' keeps none — the solo-language experiment knob (language sweeps).
# Only ever narrows the builtin pair; extended languages ride LANGUAGES_ENV.
BASE_LANGUAGES_ENV = "GRIM_BASE_LANGUAGES"


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


@dataclass(frozen=True)
class Runner:
    """How to execute one language: temp-file suffix, argv builder, version
    probe, the primary binary (for `grim doctor`), and an optional OS gate
    (sys.platform values; None = runs on any platform)."""

    suffix: str
    build_argv: Callable[[Path], list[str]]
    version_argv: list[str]
    tool: str
    platforms: tuple[str, ...] | None = None


def _argv(binary: str, *prefix: str) -> Callable[[Path], list[str]]:
    """An argv builder that runs BINARY on the temp script, with any fixed
    PREFIX flags (e.g. `go run`) before the path."""
    return lambda path: [binary, *prefix, str(path)]


def _shlex_quote(text: str) -> str:
    """Single-quote a path for embedding inside a CLI dot-command argument:
    sqlite3/duckdb `.read` tokenize the rest of the line, so a space in a
    temp path must be quoted (a separate argv element is rejected with
    'Usage: .read FILE')."""
    return "'%s'" % text.replace("'", "'\\''")


def _read_argv(binary: str) -> Callable[[Path], list[str]]:
    """An argv builder whose binary reads a dot-command FILE inline, e.g.
    `sqlite3 :memory: ".read FILE"` — one argv element, path quoted."""
    return lambda path: [binary, ":memory:", ".read %s" % _shlex_quote(str(path))]


# language -> Runner. The builtin pair is always available; the extended
# catalog (docs/languages.md) is opt-in via GRIM_LANGUAGES (config [languages])
# and platform-gated where an interpreter is OS-specific (osascript -> darwin).
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
_BUILTIN_RUNNERS: dict[str, Runner] = {
    "bash": Runner(".sh", _argv("bash"), ["bash", "--version"], tool="bash"),
    "python": Runner(
        ".py",
        lambda path: ["uv", "run", "--no-project", str(path)],
        ["uv", "--version"],
        tool="uv",
    ),
}

_EXTENDED_RUNNERS: dict[str, Runner] = {
    "janet": Runner(".janet", _argv("janet"), ["janet", "--version"], tool="janet"),
    "racket": Runner(".rkt", _argv("racket"), ["racket", "--version"], tool="racket"),
    "hy": Runner(".hy", _argv("hy"), ["hy", "--version"], tool="hy"),
    "nim": Runner(".nim", _argv("nim", "r"), ["nim", "--version"], tool="nim"),
    "ruby": Runner(".rb", _argv("ruby"), ["ruby", "--version"], tool="ruby"),
    # js/ts via bun (docs/languages.md: "bun (node)") — bun runs both.
    "bun": Runner(".ts", _argv("bun"), ["bun", "--version"], tool="bun"),
    "php": Runner(".php", _argv("php"), ["php", "--version"], tool="php"),
    "go": Runner(".go", _argv("go", "run"), ["go", "version"], tool="go"),
    "perl": Runner(".pl", _argv("perl"), ["perl", "--version"], tool="perl"),
    "jq": Runner(".jq", _argv("jq", "-f"), ["jq", "--version"], tool="jq"),
    # sql via the sqlite3 CLI: the script is `.read` inline (see _read_argv).
    "sql": Runner(".sql", _read_argv("sqlite3"), ["sqlite3", "--version"], tool="sqlite3"),
    "awk": Runner(".awk", _argv("awk", "-f"), ["awk", "--version"], tool="awk"),
    # macOS-only: AppleScript needs the OS's osascript interpreter.
    "osascript": Runner(
        ".applescript",
        _argv("osascript"),
        ["osascript", "-e", "return (system version of (system info))"],
        tool="osascript",
        platforms=("darwin",),
    ),
    "lua": Runner(".lua", _argv("lua"), ["lua", "-v"], tool="lua"),
    "luajit": Runner(".lua", _argv("luajit"), ["luajit", "-v"], tool="luajit"),
    "fennel": Runner(".fnl", _argv("fennel"), ["fennel", "--version"], tool="fennel"),
    "zig": Runner(".zig", _argv("zig", "run"), ["zig", "version"], tool="zig"),
    # duckdb + prql: SQL-ish execution; duckdb mirrors sqlite3's .read, and
    # prql compiles to SQL, piped straight into duckdb to actually run.
    "duckdb": Runner(".sql", _read_argv("duckdb"), ["duckdb", "--version"], tool="duckdb"),
    "prql": Runner(
        ".prql",
        lambda path: [
            "bash",
            "-c",
            'prqlc compile --no-color "$1" | duckdb :memory:',
            "prql",
            str(path),
        ],
        ["prqlc", "--version"],
        tool="prqlc",
    ),
    "typst": Runner(
        ".typ",
        lambda path: ["typst", "compile", str(path), str(path) + ".pdf"],
        ["typst", "--version"],
        tool="typst",
    ),
    "bc": Runner(".bc", _argv("bc"), ["bc", "--version"], tool="bc"),
    "dc": Runner(".dc", _argv("dc"), ["dc", "--version"], tool="dc"),
    # tclsh has no --version flag; `puts [info patchlevel]` piped over its own
    # stdin (via bash -c) reports it without depending on the caller's stdin,
    # which _env_fingerprint never sets. Ref: https://www.tcl-lang.org/man/tcl8.6/TclCmd/info.htm
    "tcl": Runner(
        ".tcl",
        _argv("tclsh"),
        ["bash", "-c", "echo 'puts [info patchlevel]' | tclsh"],
        tool="tclsh",
    ),
    # expect(1) automates interactive programs by scripting a pty; a plain
    # `expect FILE` (no -f needed) runs it same as any other file-argv runner.
    # Ref: https://linux.die.net/man/1/expect
    "expect": Runner(".exp", _argv("expect"), ["expect", "-v"], tool="expect"),
}

_ALL_RUNNERS: dict[str, Runner] = {**_BUILTIN_RUNNERS, **_EXTENDED_RUNNERS}


# Public so verbs/write.py can reject unsupported languages at write
# time instead of only failing later here at run time.
def requested_languages() -> frozenset[str]:
    """Extended languages named in $GRIM_LANGUAGES (seeded by config.py from
    config.toml's [languages] table) BEFORE platform filtering — lets `grim
    doctor` explain why one was skipped. Unset/empty -> empty set."""
    raw = os.environ.get(LANGUAGES_ENV, "")
    return frozenset(tok.strip() for tok in raw.split(",") if tok.strip())


def enabled_languages() -> frozenset[str]:
    """Extended languages the user opted into that are ALSO valid on this OS.
    Everything is off by default: no config -> empty set. Never includes a
    name outside the catalog (external input, filtered not asserted)."""
    enabled = frozenset(
        lang
        for lang in requested_languages()
        if lang in _EXTENDED_RUNNERS and _platform_ok(_EXTENDED_RUNNERS[lang])
    )
    assert enabled <= frozenset(_EXTENDED_RUNNERS), "enabled is a subset of the catalog"
    return enabled


def base_languages() -> frozenset[str]:
    """Builtin languages the agent may WRITE: unset -> both (status quo);
    set -> the named subset of {python, bash} — '' removes the builtins
    entirely, the solo-language experiment knob. Unknown names are filtered,
    never asserted (external input). Execution of scripts already in the
    library is not gated by this — the same writing-only rule as the
    extended-language toggle."""
    raw = os.environ.get(BASE_LANGUAGES_ENV)
    if raw is None:
        return frozenset(_BUILTIN_RUNNERS)
    assert isinstance(raw, str), "environment values are strings"
    tokens = frozenset(tok.strip() for tok in raw.split(",") if tok.strip())
    base = tokens & frozenset(_BUILTIN_RUNNERS)
    assert base <= frozenset(_BUILTIN_RUNNERS), "base only ever narrows the builtin pair"
    return base


def supported_languages() -> frozenset[str]:
    """Languages `grim write --lang` accepts right now: the (subsettable)
    builtins plus enabled, platform-valid extended languages. Fail-safe: if
    the two knobs together empty the set, fall back to the builtin pair —
    an agent that can write nothing is an operator error, never a brick."""
    supported = base_languages() | enabled_languages()
    if not supported:
        return frozenset(_BUILTIN_RUNNERS)
    assert supported <= frozenset(_ALL_RUNNERS), "supported stays within the catalog"
    assert supported, "the writable set is never empty"
    return supported


def language_tool(language: str) -> str:
    """Primary binary for LANGUAGE (its `grim doctor` availability probe), or
    '' for a language outside the catalog."""
    runner = _ALL_RUNNERS.get(language)
    return runner.tool if runner is not None else ""


def language_status(language: str) -> str:
    """Why LANGUAGE is (or isn't) runnable here, for `grim doctor`: 'ready',
    an unknown-catalog note, or a platform-mismatch note."""
    runner = _EXTENDED_RUNNERS.get(language)
    if runner is None:
        return "unknown language (not in the extended catalog)"
    if not _platform_ok(runner):
        return "not supported on %s — requires %s" % (
            sys.platform,
            ", ".join(runner.platforms or ()),
        )
    return "ready"


def _platform_ok(runner: Runner) -> bool:
    """A runner without a platform gate runs anywhere; a gated one only on the
    listed sys.platform values (osascript: darwin)."""
    return runner.platforms is None or sys.platform in runner.platforms


def _env_fingerprint(version_argv: list[str]) -> str:
    """`<tool> --version`, first line only — cheap and always available.

    Not persisted here (stateless invariant); the caller is responsible for
    storing it on the execution row so Phase 4's gardener can use it for
    staleness triage. The probe is a harness-internal system call, so it is
    hard-bounded: DEVNULL stdin (a probe must never block reading OUR fd —
    the 120s seed-hang class) and an explicit timeout, marker string on
    expiry (external binary => validate, never assert).
    """
    try:
        result = subprocess.run(
            version_argv,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=_VERSION_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"{version_argv[0]}:version-probe-timed-out"
    output = result.stdout or result.stderr
    first_line = output.splitlines()[0] if output else ""
    return f"{version_argv[0]}:{first_line}"


def dispatch(script_version: ScriptVersion, request: ExecutionRequest) -> ExecutionResult:
    """Run `script_version.body` under the runner for its language.

    Raises ValueError for a language outside the catalog or one that is
    catalogued but invalid on this platform — `language` is external input
    (came from `grim write --lang`), so this is real validation, not an
    assertion (root CLAUDE.md §3). A catalogued language runs even when
    currently disabled in config: the toggle gates *writing* new scripts
    (supported_languages), not executing ones already in the library.
    """
    assert request.timeout > 0, "timeout must be positive"
    runner = _ALL_RUNNERS.get(script_version.language)
    if runner is None:
        supported = ", ".join(sorted(_ALL_RUNNERS))
        raise ValueError(
            f"unsupported language {script_version.language!r} — supported: {supported}"
        )
    if not _platform_ok(runner):
        raise ValueError(
            f"language {script_version.language!r} is not supported on this platform "
            f"({sys.platform!r}) — requires {runner.platforms!r}"
        )
    fingerprint = _env_fingerprint(runner.version_argv)

    with tempfile.NamedTemporaryFile(mode="w", suffix=runner.suffix, delete=True) as f:
        f.write(script_version.body)
        f.flush()
        command = runner.build_argv(Path(f.name)) + request.argv
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


def _stdin_is_tty() -> bool:
    """True only when the process stdin is a live interactive terminal.
    Tolerates the harness's StringIO swap (isatty() -> False) and a closed
    or absent sys.stdin (detached daemons) — both mean "not interactive".
    Ref: https://docs.python.org/3/library/io.html#io.IOBase.isatty
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except ValueError:  # isatty() on a closed file
        return False


def _child_stdin(stdin: str | None) -> int | None:
    """What the child's stdin fd should be. Negative space (the 2026-08-12
    seed hangs): a script that reads stdin must always reach EOF — handing
    it our own non-tty fd (an agent pipe that never closes) blocks
    sys.stdin.read() until the timeout. PIPE when we have bytes to feed,
    inherit only from a real terminal (a human provides the EOF), DEVNULL
    (instant EOF) otherwise.
    Ref: https://docs.python.org/3/library/subprocess.html#subprocess.DEVNULL
    """
    if stdin is not None:
        return subprocess.PIPE
    if _stdin_is_tty():
        return None  # interactive CLI: the human types, Ctrl-D ends
    return subprocess.DEVNULL


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
    child_stdin = _child_stdin(stdin)
    # Re-derived independently of _child_stdin's branches so any future edit
    # that reintroduces fd inheritance outside a tty fails loudly right here.
    assert child_stdin is not None or _stdin_is_tty() is False, (
        "stdin fd policy violated: inherit only from an interactive tty"
    )
    assert child_stdin is not None or stdin is None, (
        "provided stdin must travel via PIPE, never be dropped"
    )
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdin=child_stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        start_new_session=True,
    )
    spawn_ms = (time.monotonic() - started) * 1000
    assert spawn_ms < _SPAWN_BUDGET_MS, (
        f"process spawn took {spawn_ms:.0f}ms (budget {_SPAWN_BUDGET_MS}ms) — spawning is a fast "
        "system call; a slow spawn means the platform is unhealthy, not the script"
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
