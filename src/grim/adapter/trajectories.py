"""Where grim-agent writes per-run trajectory files, and how many it keeps
(adapter slice).

Trajectories used to land in the system temp dir and vanish on reboot: of
~100 traced sessions only one transcript survived, so format errors and
dead sessions could not be diagnosed after the fact. They now default to
~/.grimoire/trajectories, and each launch prunes grim's own files there
down to the newest $GRIM_TRAJ_KEEP (default 500) — an explicit bound, never
an ever-growing pile. $GRIM_TRAJ_DIR (config.toml `traj_dir`) still wins.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TRAJ_DIR_ENV = "GRIM_TRAJ_DIR"
TRAJ_KEEP_ENV = "GRIM_TRAJ_KEEP"
DEFAULT_KEEP = 500
_KEEP_MIN = 1  # the newest file is what --continue's pointer reads
_KEEP_MAX = 100_000
# Only grim's own files are ever pruned — launcher.py names them
# grimoire-<stamp>-<pid>, run.sh grimoire-mini-<stamp>-<pid>. A shared dir
# like /tmp may hold other tools' trajectories; those are never touched.
_GLOB = "grimoire-*.traj.json"
_SUFFIX = ".traj.json"


def trajectory_dir() -> Path:
    """$GRIM_TRAJ_DIR when set, else ~/.grimoire/trajectories — always
    absolute, so a relative env value can't drift with the process cwd."""
    raw = os.environ.get(TRAJ_DIR_ENV, "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".grimoire" / "trajectories"
    absolute = base.absolute()
    assert absolute.is_absolute(), "trajectory dir is absolute"
    assert absolute.name or raw, "the default dir always has a name"
    return absolute


def keep_limit() -> int:
    """$GRIM_TRAJ_KEEP clamped to [1, 100_000]; unset or non-numeric means
    the default. Env is external input: validated, never asserted."""
    raw = os.environ.get(TRAJ_KEEP_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_KEEP
    except ValueError:
        value = DEFAULT_KEEP
    clamped = max(_KEEP_MIN, min(_KEEP_MAX, value))
    assert _KEEP_MIN <= clamped <= _KEEP_MAX, "keep limit is clamped"
    return clamped


def prune(directory: Path, keep: int) -> int:
    """Delete grim's oldest trajectory files in `directory` beyond the newest
    `keep` (by mtime). Returns how many were deleted. A file that vanishes
    or can't be removed is skipped — pruning must never block a launch.
    Ref: https://docs.python.org/3/library/pathlib.html#pathlib.Path.glob
    """
    assert keep >= _KEEP_MIN, "always keep at least the newest trajectory"
    try:
        files = sorted(directory.glob(_GLOB), key=_mtime, reverse=True)
    except OSError:
        return 0
    deleted = 0
    for stale in files[keep:]:
        assert stale.name.endswith(_SUFFIX), "only trajectory files are pruned"
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            continue
        deleted += 1
    assert deleted <= max(0, len(files) - keep), "never deletes a kept file"
    return deleted


def _mtime(path: Path) -> float:
    """mtime for sorting; a file deleted mid-sort sorts oldest."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def next_path(stamp: str, pid: int) -> str:
    """A fresh trajectory path for this run, after pruning old ones. The dir
    is created on demand; if it can't be (read-only HOME), fall back to the
    system temp dir rather than failing the launch."""
    assert stamp, "a run stamp is required"
    assert pid > 0, "a real process id is required"
    directory = trajectory_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path(tempfile.gettempdir())
    prune(directory, keep_limit())
    path = str(directory / f"grimoire-{stamp}-{pid}{_SUFFIX}")
    assert path.endswith(_SUFFIX), "trajectory path must be a .traj.json file"
    return path
