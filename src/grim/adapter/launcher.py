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
import os
import sys
import tempfile
import time
from importlib import resources
from typing import Any

from grim import cli

# grimoire.yaml ships inside this package (see pyproject hatchling config);
# resources.files locates it whether grim runs from source or an installed
# wheel. Ref: https://docs.python.org/3/library/importlib.resources.html
_CONFIG_PACKAGE = "grim.adapter"
_CONFIG_NAME = "grimoire.yaml"
_MISSING_EXTRA_HINT = 'grim-agent needs the "agent" extra: uv tool install "grimoire[agent]"'
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

The harness always runs unattended (yolo + exit-immediately are forced on).

Common options:
  -m, --model MODEL    Model to use (falls back to $GRIM_MODEL).
  -t, --task TEXT      Task/problem statement (or pass it as the first arg).
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


def build_mini_args(
    user_argv: list[str], config: str, trajectory: str, model_default: str | None = None
) -> list[str]:
    """Assemble the argv handed to mini's Typer app. Pure (no I/O) so it is
    unit-testable without launching the loop.

    Contract, mirroring run.sh: a bare leading token is the task (so
    `grim-agent "do X"` works); everything else forwards to mini verbatim.
    The harness is always unattended — `-y` (yolo) and `--exit-immediately`
    are forced on, and our config is prepended (mini merges multiple `-c`,
    so a user's own `-c extra.yaml` still layers on top). `-o` and `-m` are
    injected only when the user did not supply their own, so an explicit
    flag always wins over the `$GRIM_MODEL` / trajectory defaults.
    """
    assert config, "config path required"
    assert trajectory, "trajectory path required"
    args: list[str] = ["-c", config, "-y", "--exit-immediately"]
    rest = list(user_argv)
    if rest and not rest[0].startswith("-"):
        args += ["-t", rest[0]]  # ergonomic positional task -> mini's -t/--task
        rest = rest[1:]
    args += rest
    if not _has_flag(args, "-o", "--output"):
        args += ["-o", trajectory]
    # $GRIM_MODEL is a default, not an override: skip it if the user passed -m.
    if model_default and not _has_flag(args, "-m", "--model"):
        args += ["-m", model_default]
    assert "-y" in args and "--exit-immediately" in args, "harness runs must be unattended"
    return args


def main(argv: list[str] | None = None) -> int:
    """Console-script entry (pyproject `grim-agent`). Init the library, then
    hand off to mini's loop in-process."""
    raw = list(sys.argv[1:] if argv is None else argv)
    assert isinstance(raw, list), "argv must resolve to a list"

    # Grim-branded help: answer before init or the mini import, so `--help`
    # never migrates the DB, seeds the library, or shows mini's Typer screen.
    if _has_token(raw, "-h", "--help"):
        print(_GRIM_HELP)
        return 0

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

    print(_GRIM_BANNER, file=sys.stderr)  # concise, stdout stays clean for the answer

    # Idempotent migrate + seed via the kernel CLI. Its progress lines go to
    # stderr so stdout stays clean for the agent's final answer.
    with contextlib.redirect_stdout(sys.stderr):
        init_code = cli.main(["init"])
    if init_code != 0:
        print("grim-agent: library init failed (see above)", file=sys.stderr)
        return init_code

    # $GRIM_MODEL supplies the model when the user didn't pass -m, matching the
    # container entrypoint and giving `export GRIM_MODEL=…; grim-agent "task"`.
    mini_args = build_mini_args(
        raw, _config_path(), _trajectory_path(), model_default=os.environ.get("GRIM_MODEL")
    )
    try:
        # Typer app.__call__ -> click main(args=..., standalone_mode=False):
        # returns instead of sys.exit, so real errors propagate (fail loud).
        app(args=mini_args, standalone_mode=False)
    except SystemExit as exc:  # usage errors still raise SystemExit
        return _exit_code(exc)
    return 0
