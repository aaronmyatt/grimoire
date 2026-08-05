"""`grim-agent` — the installable, yolo-mode harness entry point.

This is what makes grim distributable as a standalone agent harness (like
`mini` or `pi`): after `uv tool install "grimoire[agent]"`, a single command
runs the autonomous loop with no repo checkout, no `run.sh`, and no
`mini -c <yaml>` incantation:

    grim-agent "summarize README.md" -m anthropic/claude-sonnet-4-5

Design constraints honored here:
- **No subprocess.** The adapter slice's invariant forbids adding a shell to
  the control plane (that would reopen the bypass D7 exists to close). We
  drive mini-swe-agent's own Typer app *in-process* instead.
- **Slice -> kernel only.** DB init routes through `grim.cli.main(["init"])`
  (the kernel), never through the seeds slice directly — slices don't import
  each other (root CLAUDE.md §2).
- **Runs installed or from source.** grimoire.yaml is a packaged data file
  located via importlib.resources, so the path resolves from a wheel too.
"""

from __future__ import annotations

import contextlib
import enum
import json
import os
import sys
import tempfile
import time
from importlib import resources
from typing import Any, NamedTuple

from grim import cli, db

# grimoire.yaml ships inside this package (see pyproject hatchling config);
# resources.files locates it whether grim runs from source or an installed
# wheel. Ref: https://docs.python.org/3/library/importlib.resources.html
_CONFIG_PACKAGE = "grim.adapter"
_CONFIG_NAME = "grimoire.yaml"
_MISSING_EXTRA_HINT = (
    'grim-agent needs the "agent" extra: uv tool install "grimoire[agent]"'
    " (or, from a checkout: uv run --extra agent grim-agent)\n"
    "Once tool-installed it is on PATH outside uv: GRIM_MODEL=... grim-agent '<task>'"
)
MISSING_EXTRA_EXIT_CODE = 2  # returned when the optional `agent` extra isn't importable

# One-line startup banner, replacing mini's version/migration/config-path
# preamble (silenced via MSWEA_SILENT_STARTUP below). Goes to stderr so stdout
# stays clean for the agent's final answer.
_GRIM_BANNER = "grim-agent — script-hoarding agent harness (six verbs over a SQLite library)"

# Grim-branded --help, shown INSTEAD of mini's Typer help. Deliberately covers
# only grim's own ergonomics/flags/env surface; the full underlying option
# list is one `--mini-help` away. Printed before any DB init or mini import, so
# `--help` never touches the library.
_GRIM_HELP = """\
grim-agent — a script-hoarding agent harness on mini-swe-agent.

Runs an autonomous agent whose action space is six verbs over a searchable
SQLite library — write, update, read, list, find, run — instead of raw bash.
Every action becomes a named, versioned script that the agent (and you) can
find and reuse on later runs.

Usage:
  grim-agent "<task>" -m <model>
  grim-agent -t "<task>" -m anthropic/claude-sonnet-4-5

Runs yolo (no per-action confirmation). At a terminal it's attended — on
finish it prompts for a follow-up task so you can keep steering; a
piped/redirected run (containers, cron, CI) exits immediately instead.

Common options:
  -m, --model MODEL    Model to use (falls back to $GRIM_MODEL).
  -t, --task TEXT      Task/problem statement (or pass it as the first arg).
  -p, --print          One-shot programmatic mode: print only the agent's
                       final answer to stdout (all UI to stderr), then exit
                       without prompting. Ideal for pipes, scripts, and evals.
      --output-format text|json
                       With -p, pick the stdout shape — `text` (bare answer,
                       default) or `json` ({result, exit_status, cost,
                       api_calls, trajectory}). Implies -p.
      --continue       Warm-start from your library: frontload recently
                       valuable scripts into the task (GRIM_RECALL_LIMIT, ~10)
                       and reuse the last session's lineage. Off by default.
  -l, --cost-limit N   Cost limit in USD; 0 disables.
  -o, --output PATH    Trajectory JSON (default: a fresh per-run file under
                       $GRIM_TRAJ_DIR or the system temp dir).
  -c, --config SPEC    Extra mini config file / key=value, merged on top of
                       grim's own packaged config.

Environment & config:
  GRIM_MODEL               Default model when -m is omitted.
  GRIM_DB                  Path to the grimoire SQLite library.
  GRIM_TRAJ_DIR            Directory for per-run trajectory files.
  ~/.grimoire/system.md    Operator instructions injected into every run.
  ~/.grimoire/config.toml  Seed and env-var defaults for the library.

All other flags forward to the underlying mini-swe-agent harness.
See its full option list with:  grim-agent --mini-help\
"""


def _config_path() -> str:
    """Absolute path to the packaged grimoire.yaml.

    Caveat: assumes a filesystem-backed install (uv tool / pip unzip wheels
    to a directory). A zipimport install would need `resources.as_file` with
    a context kept open across the app() call; not supported here.
    """
    path = str(resources.files(_CONFIG_PACKAGE) / _CONFIG_NAME)
    assert path.endswith(_CONFIG_NAME), "config path must point at grimoire.yaml"
    assert os.path.exists(path), f"packaged config missing: {path}"
    return path


def _trajectory_path() -> str:
    """A fresh trajectory file per run so a crash leaves an inspectable
    transcript — mini overwrites one fixed path by default (adapter/run.sh).
    Honors $GRIM_TRAJ_DIR, else the system temp dir."""
    base = os.environ.get("GRIM_TRAJ_DIR") or tempfile.gettempdir()
    path = os.path.join(base, f"grimoire-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.traj.json")
    assert base, "trajectory dir must be non-empty"
    assert path.endswith(".traj.json"), "trajectory path must be a .traj.json file"
    return path


def _has_token(args: list[str], *tokens: str) -> bool:
    """True if any bare token in `tokens` appears verbatim in args — used to
    spot help-style flags (`-h`, `--help`, `--mini-help`) before dispatch."""
    assert tokens, "at least one token to match"
    present = any(a in tokens for a in args)
    assert isinstance(present, bool), "token presence is a bool"
    return present


def _exit_code(exc: SystemExit) -> int:
    """Map a click/Typer SystemExit to a shell return code: None -> 0, an int
    verbatim, anything else -> 1 (fail loud but bounded)."""
    code = exc.code
    resolved = code if isinstance(code, int) else (0 if code is None else 1)
    assert isinstance(resolved, int), "exit code must be an int"
    return resolved


def _run_help_app(app: Any) -> int:
    """Invoke mini's Typer app with `--help` and map its SystemExit to a shell
    code — the escape hatch behind `grim-agent --mini-help`."""
    assert app is not None, "mini app required"
    try:
        app(args=["--help"], standalone_mode=False)
    except SystemExit as exc:
        return _exit_code(exc)
    return 0


def _has_flag(args: list[str], short: str, long: str) -> bool:
    """True if `-x`, `--xxx`, `-x=v`, or `--xxx=v` appears in args — used to
    honor a user-supplied flag over an injected default."""
    assert short.startswith("-") and long.startswith("--"), "flag names must be dashed"
    present = any(a in (short, long) or a.startswith((short + "=", long + "=")) for a in args)
    assert isinstance(present, bool), "flag presence is a bool"
    return present


def _take_flag(argv: list[str], flag: str) -> tuple[bool, list[str]]:
    """Remove a bare boolean flag from argv, reporting whether it was present.
    Used for grim-only flags mini's Typer app doesn't accept (e.g. --continue),
    so they never reach dispatch."""
    assert flag.startswith("--"), "flag must be long-form"
    present = flag in argv
    rest = [a for a in argv if a != flag]
    assert len(rest) <= len(argv), "stripping never grows argv"
    return present, rest


class LaunchSpec(NamedTuple):
    """The non-argv inputs to `build_mini_args`, bundled into one struct so the
    builder stays within the 4-parameter budget (CLAUDE.md §1).

    `interactive` selects the run's finish behavior: False (the default,
    unattended) forces `--exit-immediately`; True (an attended terminal) keeps
    mini's post-submit prompt so the human can add a follow-up task.
    Ref (NamedTuple): https://docs.python.org/3/library/typing.html#typing.NamedTuple
    """

    config: str
    trajectory: str
    model_default: str | None = None
    interactive: bool = False
    cost_default: str | None = None  # $GRIM_COST_LIMIT -> mini's -l/--cost-limit
    step_default: str | None = None  # $GRIM_STEP_LIMIT -> mini's -c agent.step_limit
    session_id: str | None = None  # --continue -> mini's -c environment.session_id


def build_mini_args(user_argv: list[str], spec: LaunchSpec) -> list[str]:
    """Assemble the argv handed to mini's Typer app. Pure (no I/O) so it is
    unit-testable without launching the loop.

    Contract, mirroring run.sh: a bare leading token is the task (so
    `grim-agent "do X"` works); everything else forwards to mini verbatim.
    The run is always yolo (`-y`), and our config is prepended (mini merges
    multiple `-c`, so a user's own `-c extra.yaml` still layers on top).
    `--exit-immediately` is forced on only for UNATTENDED runs
    (`spec.interactive` False); an attended terminal keeps mini's post-submit
    "type a new task or Enter to quit" prompt (confirm_exit defaults True), so
    the human can steer without losing the session. `-o` and `-m` are injected
    only when the user did not supply their own, so an explicit flag always
    wins over the `$GRIM_MODEL` / trajectory defaults.
    """
    assert spec.config, "config path required"
    assert spec.trajectory, "trajectory path required"
    args: list[str] = ["-c", spec.config, "-y"]
    if not spec.interactive:
        # Unattended: skip mini's finish prompt so containers/cron never block
        # on stdin. Maps to confirm_exit=False in minisweagent/run/mini.py.
        args += ["--exit-immediately"]
    if spec.step_default:
        # step_limit has no CLI flag; inject it via mini's -c config merge,
        # before the user's argv so a user-supplied -c still layers on top.
        args += ["-c", f"agent.step_limit={spec.step_default}"]
    if spec.session_id:
        # --continue: reuse the last agent session so this run's executions
        # extend its lineage (seq + affinity). GrimEnvironmentConfig.session_id
        # honors it; before the user's argv so a user's own -c can override.
        args += ["-c", f"environment.session_id={spec.session_id}"]
    rest = list(user_argv)
    if rest and not rest[0].startswith("-"):
        args += ["-t", rest[0]]  # ergonomic positional task -> mini's -t/--task
        rest = rest[1:]
    args += rest
    if not _has_flag(args, "-o", "--output"):
        args += ["-o", spec.trajectory]
    # $GRIM_MODEL is a default, not an override: skip it if the user passed -m.
    if spec.model_default and not _has_flag(args, "-m", "--model"):
        args += ["-m", spec.model_default]
    # $GRIM_COST_LIMIT is a default too: skip it if the user passed their own -l.
    if spec.cost_default and not _has_flag(args, "-l", "--cost-limit"):
        args += ["-l", spec.cost_default]
    assert "-y" in args, "harness runs are always yolo"
    assert spec.interactive or "--exit-immediately" in args, "unattended runs must exit immediately"
    return args


# ---------------------------------------------------------------------------
# Programmatic print mode (-p / --output-format). Gives a clean, pipeable
# stdout — only the agent's final answer — by sending mini's interactive UI to
# stderr and reading the result back out of the saved trajectory. This is what
# makes grim-agent usable from a script, a pipe, or an eval Runner.
# ---------------------------------------------------------------------------

# mini stamps this into the trajectory's info.exit_status on a clean finish
# (minisweagent/environments/local.py); anything else means "no answer."
_SUBMITTED_STATUS = "Submitted"
_PRINT_FLAGS = ("-p", "--print")
_FORMAT_FLAG = "--output-format"


class OutputFormat(enum.Enum):
    """How `-p` renders the answer on stdout. An enum (not a bare string) so an
    invalid value is rejected at the flag boundary rather than flowing inward
    (CLAUDE.md §3, parse-don't-validate). Ref:
    https://docs.python.org/3/library/enum.html#enum.Enum"""

    TEXT = "text"
    JSON = "json"


class PrintOptions(NamedTuple):
    """The print flags after they've been split out of argv."""

    enabled: bool
    output_format: OutputFormat


class RunSummary(NamedTuple):
    """A run's gradeable outcome, read from mini's trajectory `info` block
    (minisweagent/agents/default.py:serialize)."""

    result: str | None  # None when the agent never submitted (limit/error)
    exit_status: str
    cost: float
    api_calls: int
    trajectory: str


def _output_format(value: str) -> OutputFormat:
    """Parse a `--output-format` value into the enum, failing loud (SystemExit
    2, the launcher's usage-error convention) on anything unrecognised."""
    assert isinstance(value, str), "format value must be a string"
    try:
        return OutputFormat(value)
    except ValueError:
        choices = "|".join(fmt.value for fmt in OutputFormat)
        print(f"grim-agent: invalid {_FORMAT_FLAG} '{value}' (want {choices})", file=sys.stderr)
        raise SystemExit(2) from None


def _consume_format(argv: list[str], i: int) -> tuple[OutputFormat, int]:
    """Resolve the format value at index `i`, joined (`--output-format=json`)
    or spaced (`--output-format json`). Returns the format and the index of the
    last token consumed."""
    assert 0 <= i < len(argv), "index must point at the format flag"
    token = argv[i]
    if "=" in token:
        return _output_format(token.split("=", 1)[1]), i
    if i + 1 >= len(argv):
        print(f"grim-agent: {_FORMAT_FLAG} needs a value (text|json)", file=sys.stderr)
        raise SystemExit(2)
    return _output_format(argv[i + 1]), i + 1


def parse_print_options(argv: list[str]) -> tuple[PrintOptions, list[str]]:
    """Split grim-only print flags out of `argv`, returning the parsed options
    plus the argv that should still forward to mini. `--output-format` implies
    `-p`. The six-verb agent contract is untouched: these flags are the human
    launcher's, never forwarded to (or seen by) the model."""
    assert isinstance(argv, list), "argv must be a list"
    enabled = False
    fmt = OutputFormat.TEXT
    rest: list[str] = []
    i = 0
    while i < len(argv):  # bounded: fixed-length argv, i strictly increases
        token = argv[i]
        if token in _PRINT_FLAGS:
            enabled = True
        elif token == _FORMAT_FLAG or token.startswith(_FORMAT_FLAG + "="):
            enabled = True
            fmt, i = _consume_format(argv, i)
        else:
            rest.append(token)
        i += 1
    assert isinstance(fmt, OutputFormat), "format resolves to the enum"
    return PrintOptions(enabled=enabled, output_format=fmt), rest


def parse_result(trajectory: dict[str, Any]) -> str | None:
    """The agent's submitted answer, or None if it never finished cleanly.
    Gated on info.exit_status so a limit/error run reports *no* answer rather
    than an empty string that would look like a real (blank) response."""
    assert isinstance(trajectory, dict), "trajectory must be a dict"
    info = trajectory.get("info", {})
    if info.get("exit_status") == _SUBMITTED_STATUS:
        submission = info.get("submission")
        if isinstance(submission, str):
            return submission
    return None


def summarize_run(trajectory: dict[str, Any], trajectory_path: str) -> RunSummary:
    """Fold a mini trajectory dict into the RunSummary the print modes emit."""
    assert isinstance(trajectory, dict), "trajectory must be a dict"
    assert trajectory_path, "a trajectory path is required"
    info = trajectory.get("info", {})
    stats = info.get("model_stats", {})
    return RunSummary(
        result=parse_result(trajectory),
        exit_status=str(info.get("exit_status", "")),
        cost=float(stats.get("instance_cost", 0.0) or 0.0),
        api_calls=int(stats.get("api_calls", 0) or 0),
        trajectory=trajectory_path,
    )


def format_output(summary: RunSummary, output_format: OutputFormat) -> str:
    """Render the answer for stdout. `text` is the bare result (empty when the
    agent gave none); `json` is a one-line envelope safe to pipe into `jq`."""
    assert isinstance(summary, RunSummary), "summary must be a RunSummary"
    assert isinstance(output_format, OutputFormat), "output_format must be the enum"
    if output_format is OutputFormat.TEXT:
        return summary.result or ""
    return json.dumps(
        {
            "result": summary.result,
            "exit_status": summary.exit_status,
            "cost": summary.cost,
            "api_calls": summary.api_calls,
            "trajectory": summary.trajectory,
        }
    )


def _read_trajectory(path: str) -> dict[str, Any]:
    """Load a mini trajectory JSON, or an empty dict when it is missing or
    unreadable — a run that died before writing one still yields a (no-answer)
    summary instead of a traceback (VALIDATE external input, §3)."""
    assert path, "trajectory path required"
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def run_print(
    app: Any, mini_args: list[str], trajectory_path: str, output_format: OutputFormat
) -> int:
    """Drive mini with its whole UI redirected to stderr, then write ONLY the
    final answer to real stdout in `output_format`. Returns a shell exit code:
    0 on a clean submission, nonzero otherwise, so a batch caller (the eval
    Runner) treats a no-answer run as a failure."""
    assert app is not None, "mini app required"
    assert "--exit-immediately" in mini_args, "print mode must run unattended"
    code = 0
    # rich resolves sys.stdout live, so this redirect captures mini's console UI
    # too. Ref: https://docs.python.org/3/library/contextlib.html#contextlib.redirect_stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            app(args=mini_args, standalone_mode=False)
        except SystemExit as exc:  # usage errors still raise SystemExit
            code = _exit_code(exc)
    summary = summarize_run(_read_trajectory(trajectory_path), trajectory_path)
    sys.stdout.write(format_output(summary, output_format) + "\n")
    return 0 if summary.exit_status == _SUBMITTED_STATUS else (code or 1)


def last_agent_session_id() -> str | None:
    """The most recent agent session's id, or None on a fresh library.
    `--continue` reuses it as environment.session_id so this run's executions
    extend that session's lineage (seq + affinity) instead of starting fresh.
    Read-only; the library is already migrated by the time this runs."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM session WHERE kind = 'agent' "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def _launch(app: Any, raw: list[str], print_opts: PrintOptions, continue_on: bool) -> int:
    """Build mini's argv and run the agent — in print mode (clean stdout) or
    mini's normal interactive path. Split from main() so each stays within the
    function-length budget. `continue_on` (--continue) turns on library recall
    and reuses the last agent session's lineage."""
    assert app is not None, "mini app required"
    assert isinstance(raw, list), "argv must be a list"
    session_id: str | None = None
    if continue_on:
        os.environ["GRIM_RECALL"] = "1"  # read by GrimAgent.run to frontload recall
        session_id = last_agent_session_id()
    # Attended at a terminal; unattended when stdin is piped/redirected OR the
    # human asked for print mode (which must never block on a prompt). Ref:
    # https://docs.python.org/3/library/io.html#io.IOBase.isatty
    interactive = sys.stdin.isatty() and not print_opts.enabled
    spec = LaunchSpec(
        config=_config_path(),
        trajectory=_trajectory_path(),
        model_default=os.environ.get("GRIM_MODEL"),
        interactive=interactive,
        cost_default=os.environ.get("GRIM_COST_LIMIT"),
        step_default=os.environ.get("GRIM_STEP_LIMIT"),
        session_id=session_id,
    )
    mini_args = build_mini_args(raw, spec)
    if print_opts.enabled:
        return run_print(app, mini_args, spec.trajectory, print_opts.output_format)
    try:
        # Typer app.__call__ -> click main(args=..., standalone_mode=False):
        # returns instead of sys.exit, so real errors propagate (fail loud).
        app(args=mini_args, standalone_mode=False)
    except SystemExit as exc:  # usage errors still raise SystemExit
        return _exit_code(exc)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry (pyproject `grim-agent`). Parse grim's own flags,
    init the library, then hand off to mini's loop in-process."""
    raw = list(sys.argv[1:] if argv is None else argv)
    assert isinstance(raw, list), "argv must resolve to a list"

    # Grim-branded help: answer before init or the mini import, so `--help`
    # never migrates the DB, seeds the library, or shows mini's Typer screen.
    if _has_token(raw, "-h", "--help"):
        print(_GRIM_HELP)
        return 0

    # Split grim-only flags out up front; the remainder forwards to mini. A bad
    # --output-format value fails here; --continue turns on recall + session
    # lineage reuse (both applied in _launch).
    print_opts, raw = parse_print_options(raw)
    continue_on, raw = _take_flag(raw, "--continue")

    # Silence mini's import-time version/migration/config banner (guarded by
    # this env var in minisweagent/__init__.py); we print our own instead.
    os.environ["MSWEA_SILENT_STARTUP"] = "1"

    # mini is the optional `agent` extra; the console script is always
    # registered, so a core-only install lands here with a clear message
    # instead of a raw ModuleNotFoundError traceback.
    try:
        from minisweagent.run.mini import app  # noqa: PLC0415 -- lazy: extra may be absent
    except ModuleNotFoundError:
        print(_MISSING_EXTRA_HINT, file=sys.stderr)
        return MISSING_EXTRA_EXIT_CODE

    # Escape hatch: surface mini's full option list on demand, still without
    # init. `--help` short-circuits inside Typer before any config is loaded.
    if _has_token(raw, "--mini-help"):
        return _run_help_app(app)

    # Print mode keeps stdout clean for the answer, so the banner is withheld;
    # an interactive run still gets it (on stderr, so stdout stays clean too).
    if not print_opts.enabled:
        print(_GRIM_BANNER, file=sys.stderr)

    # Idempotent migrate + seed via the kernel CLI. Its progress lines go to
    # stderr so stdout stays clean for the agent's final answer.
    with contextlib.redirect_stdout(sys.stderr):
        init_code = cli.main(["init"])
    if init_code != 0:
        print("grim-agent: library init failed (see above)", file=sys.stderr)
        return init_code

    # $GRIM_MODEL supplies the model when the user didn't pass -m, matching the
    # container entrypoint and giving `export GRIM_MODEL=…; grim-agent "task"`.
    return _launch(app, raw, print_opts, continue_on)
