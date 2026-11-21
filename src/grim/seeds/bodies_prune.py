"""The run_dir_prune seed body (docs/delegation-plan.md step 5e): bounded cleanup
of the run dir. Data, not code: bodies.py's SEEDS roster wires PRUNE_SEEDS in."""

from __future__ import annotations

RUN_DIR_PRUNE = r'''"""run_dir_prune — free the run dir ($GRIM_RUN_DIR or ~/.grimoire/run) of
old job records. Only two kinds are ever deleted: a job log (JOB.log) and a job
dir (JOB/), and only when tmux has no window for JOB any more and the entry is
older than --keep-days (default 7). --max-mb M stops once the run dir is under M
MB (oldest go first). Without --yes it only lists what it would delete.
Usage: run_dir_prune [--keep-days N] [--max-mb M] [--yes]
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://docs.python.org/3/library/shutil.html#shutil.rmtree"""
import argparse
import itertools
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DAY_S, MB = 86400, 1024 * 1024
MAX_ENTRIES = 100_000  # a run dir bigger than this is listed only this far
NO_JOBS_HINTS = ("no server running", "error connecting", "can't find session")


def windows():
    """Job names tmux still has a window for (live or dead): those are never pruned."""
    socket = os.environ.get("GRIM_TMUX_SOCKET")
    base = ["tmux", "-L", socket] if socket else ["tmux"]
    try:
        listed = subprocess.run([*base, "list-windows", "-t", "=grim", "-F", "#{@grim_job}"],
                                capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        sys.exit(f"run_dir_prune: cannot ask tmux which jobs exist ({error}); deleting nothing")
    if listed.returncode != 0 and not any(hint in listed.stderr for hint in NO_JOBS_HINTS):
        sys.exit(f"run_dir_prune: tmux list-windows failed: {listed.stderr.strip()}; "
                 "deleting nothing")
    return set(listed.stdout.split())


def size_and_age(entry):
    """(bytes, newest mtime) of a log, or of a job dir and the files in it."""
    if entry.is_file():
        stat = entry.stat()
        return stat.st_size, stat.st_mtime
    paths = itertools.islice(entry.rglob("*"), MAX_ENTRIES)
    stats = [path.stat() for path in paths if path.is_file() and not path.is_symlink()]
    newest = max([entry.stat().st_mtime, *(stat.st_mtime for stat in stats)])
    return sum(stat.st_size for stat in stats), newest


parser = argparse.ArgumentParser(prog="run_dir_prune")
parser.add_argument("--keep-days", type=float, default=7.0)
parser.add_argument("--max-mb", type=float)
parser.add_argument("--yes", action="store_true", help="delete; without it only list")
opts = parser.parse_args()
if opts.keep_days < 0 or (opts.max_mb is not None and opts.max_mb < 0):
    parser.error("--keep-days and --max-mb are 0 or more")
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
if not run_dir.is_dir():
    print(f"no run dir at {run_dir}: nothing to prune")
    sys.exit(0)
live, now = windows(), time.time()
entries = list(itertools.islice(run_dir.iterdir(), MAX_ENTRIES))
sizes = {entry: size_and_age(entry) for entry in entries if not entry.is_symlink()}
total = sum(size for size, _ in sizes.values())
old = sorted((age, entry) for entry, (_, age) in sizes.items()
             if (entry.suffix == ".log" and entry.is_file()) or entry.is_dir())
freed = count = 0
for age, entry in old:
    job = entry.stem if entry.is_file() else entry.name
    if job in live or now - age < opts.keep_days * DAY_S or job.startswith("."):
        continue
    if opts.max_mb is not None and total - freed <= opts.max_mb * MB:
        break
    size = sizes[entry][0]
    if opts.yes and entry.is_dir():
        shutil.rmtree(entry)  # rmtree never follows a symlink inside the dir
    elif opts.yes:
        entry.unlink()
    print(f"{'deleted' if opts.yes else 'would delete'}  {entry}  {size / MB:.1f} MB  "
          f"{(now - age) / DAY_S:.0f}d old")
    freed, count = freed + size, count + 1
verb = "deleted" if opts.yes else "would delete (run again with --yes)"
print(f"run_dir_prune: {verb} {count} entries, {freed / MB:.1f} MB of {total / MB:.1f} MB")
'''

PRUNE_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "run_dir_prune",
        "free the run dir of old job logs and job dirs whose tmux window is gone: "
        "run_dir_prune [--keep-days N] [--max-mb M] [--yes] — lists only, without --yes",
        RUN_DIR_PRUNE,
    ),
)
