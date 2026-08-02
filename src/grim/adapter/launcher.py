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

from grim import cli

# grimoire.yaml ships inside this package (see pyproject hatchling config);
# resources.files locates it whether grim runs from source or an installed
# wheel. Ref: https://docs.python.org/3/library/importlib.resources.html
_CONFIG_PACKAGE = "grim.adapter"
_CONFIG_NAME = "grimoire.yaml"
_MISSING_EXTRA_HINT = 'grim-agent needs the "agent" extra: uv tool install "grimoire[agent]"'
MISSING_EXTRA_EXIT_CODE = 2  # returned when the optional `agent` extra isn't importable


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

    # mini is the optional `agent` extra; the console script is always
    # registered, so a core-only install lands here with a clear message
    # instead of a raw ModuleNotFoundError traceback.
    try:
        from minisweagent.run.mini import app  # noqa: PLC0415 -- lazy: extra may be absent
    except ModuleNotFoundError:
        print(_MISSING_EXTRA_HINT, file=sys.stderr)
        return MISSING_EXTRA_EXIT_CODE

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
    except SystemExit as exc:  # --help / usage errors still raise SystemExit
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 0
