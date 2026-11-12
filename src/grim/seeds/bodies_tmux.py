"""Seed bodies that drive tmux — the first surface driver (docs/surfaces.md,
docs/features/01-jobs.md). Data, not code: each constant is one seed's full
script text; bodies.py's SEEDS roster wires TMUX_SEEDS in.

A job is a tmux window in session "grim", tagged with window options
(@grim_kind, @grim_job, @grim_started). tmux itself is the registry: no pid
file, nothing to drift. The job's program is exec'd as argv — never through a
shell, same rule as bodies_exec.py. Ref: https://man.openbsd.org/tmux
"""

from __future__ import annotations

TMUX_START = r'''"""tmux_start — start a long-lived job as a tmux window in session "grim":
servers, watchers, builds, REPLs — work a normal `grim run` would kill at its
timeout. The window IS the job and tmux is the only registry (no pid file).
Usage: tmux_start JOB PROGRAM [ARG...]   (a single quoted command string is
split shlex-style). No shell: pipes, redirects, && and shell interpreters are
refused — put that logic in a python script: tmux_start JOB grim run SCRIPT.
The job runs in your cwd; its output is logged to <run_dir>/JOB.log
($GRIM_RUN_DIR or ~/.grimoire/run). See jobs with tmux_list; a human watches
with `tmux attach -t grim`. When the job ends, tmux itself signals grim-JOB:
block on that with tmux_wait grim-JOB instead of polling.
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#respawn-pane"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SESSION = "grim"
SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>&1", "&>"}
SHELLS = {"bash", "sh", "zsh", "dash", "fish", "ksh", "csh", "tcsh"}


def tmux(*args, timeout=None):
    socket = os.environ.get("GRIM_TMUX_SOCKET")
    base = ["tmux", "-L", socket] if socket else ["tmux"]
    command = [*base, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def fail(code, message):
    print(f"tmux_start: {message}", file=sys.stderr)
    sys.exit(code)


if len(sys.argv) < 3:
    print("usage: tmux_start JOB PROGRAM [ARG...]", file=sys.stderr)
    sys.exit(2)
job = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
    fail(2, f"bad job name {job!r} (letters, digits, _ and - only)")
argv = shlex.split(sys.argv[2]) if len(sys.argv) == 3 else sys.argv[2:]
operators = sorted({token for token in argv if token in SHELL_OPERATORS})
if not argv or operators or os.path.basename(argv[0]) in SHELLS:
    fail(
        2,
        f"shell lines are refused (got {operators or argv[:1]}). Start one program, or "
        "write a python script and run: tmux_start JOB grim run SCRIPT",
    )
for program in ("tmux", argv[0]):
    if shutil.which(program) is None:
        fail(127, f"program not found on PATH: {program!r}")

# An exact-match target (=grim), so a session named "grimoire" is never taken
# for ours. A failed listing just means no server or no session yet.
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", "#{@grim_job}\t#{pane_dead}")
session_exists = listed.returncode == 0
states = dict(row.split("\t") for row in listed.stdout.splitlines() if row.count("\t") == 1)
if job in states:
    state = "dead" if states[job] == "1" else "live"
    fail(1, f"job {job!r} already exists ({state}) in tmux session {SESSION!r}; pick another name")
if session_exists:
    # A wake left over from an earlier job of this name would end the next
    # tmux_wait at once. Signal, then take the signal back: the channel ends
    # empty. Ref: https://man.openbsd.org/tmux#wait-for
    tmux("wait-for", "-S", f"grim-{job}")
    try:
        tmux("wait-for", f"grim-{job}", timeout=1)
    except subprocess.TimeoutExpired:
        pass  # a live waiter took the signal instead; nothing is left over

run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
run_dir.mkdir(parents=True, exist_ok=True)
log_path = run_dir / f"{job}.log"
log_path.write_text("")  # one job, one story: a reused name starts a fresh log

# The window opens on a placeholder (`cat -` just waits) so the tags, the
# keep-when-dead flag and the log pipe are all in place before the job's first
# byte; respawn-pane -k then swaps the placeholder for the real program, run
# as argv with no shell. The pane-died hook is what signals grim-JOB.
# Ref: https://man.openbsd.org/tmux#pipe-pane https://man.openbsd.org/tmux#set-hook
cwd = os.getcwd()
create = ["new-window", "-t", f"={SESSION}:"] if session_exists else ["new-session", "-s", SESSION]
made = tmux(*create, "-d", "-n", job, "-c", cwd, "-P", "-F", "#{window_id}", "cat", "-")
if made.returncode != 0:
    fail(1, f"tmux could not open a window: {made.stderr.strip()}")
window = made.stdout.strip()
steps = [
    ["set-option", "-w", "-t", window, "remain-on-exit", "on"],
    ["set-option", "-w", "-t", window, "@grim_kind", "job"],
    ["set-option", "-w", "-t", window, "@grim_job", job],
    ["set-option", "-w", "-t", window, "@grim_started", str(int(time.time()))],
    ["pipe-pane", "-t", window, f"cat >> {shlex.quote(str(log_path))}"],
    ["set-hook", "-w", "-t", window, "pane-died", f"wait-for -S grim-{job}"],
    ["respawn-pane", "-k", "-t", window, "-c", cwd, "--", *argv],
]
for step in steps:
    done = tmux(*step)
    if done.returncode != 0:
        tmux("kill-window", "-t", window)
        fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")
print(f"started {SESSION}:{job}")
print(f"log: {log_path}")
'''

TMUX_LIST = r'''"""tmux_list — list grim's jobs: name, kind, alive or dead (with its exit
code) and age. It asks tmux itself (the tagged windows of session "grim"), so
there is no pid file or registry to drift: a job killed by hand just shows as
dead, or is gone. Usage: tmux_list   (no arguments)
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#list-windows"""
import os
import shutil
import subprocess
import sys
import time

SESSION = "grim"
FIELDS = "#{@grim_job}\t#{@grim_kind}\t#{@grim_started}\t#{pane_dead}\t#{pane_dead_status}"
NO_JOBS_HINTS = ("no server running", "error connecting", "can't find session")
MINUTE_S, HOUR_S, DAY_S = 60, 3600, 86400


def age(seconds):
    if seconds < MINUTE_S:
        return f"{seconds}s"
    if seconds < HOUR_S:
        return f"{seconds // MINUTE_S}m"
    if seconds < DAY_S:
        return f"{seconds // HOUR_S}h"
    return f"{seconds // DAY_S}d"


if len(sys.argv) != 1:
    print("usage: tmux_list  (no arguments)", file=sys.stderr)
    sys.exit(2)
if shutil.which("tmux") is None:
    print("tmux_list: program not found on PATH: 'tmux'", file=sys.stderr)
    sys.exit(127)
socket = os.environ.get("GRIM_TMUX_SOCKET")
base = ["tmux", "-L", socket] if socket else ["tmux"]
listed = subprocess.run(
    [*base, "list-windows", "-t", f"={SESSION}", "-F", FIELDS],
    capture_output=True,
    text=True,
    check=False,
)
if listed.returncode != 0 and not any(hint in listed.stderr for hint in NO_JOBS_HINTS):
    print(f"tmux_list: tmux list-windows failed: {listed.stderr.strip()}", file=sys.stderr)
    sys.exit(1)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = [row for row in rows if len(row) == 5 and row[0]]  # untagged windows are not grim's
if not jobs:
    print("no jobs")
    sys.exit(0)
now = int(time.time())
for name, kind, started, dead, status in jobs:
    state = f"dead (exit {status})" if dead == "1" else "alive"
    elapsed = age(max(0, now - int(started))) if started.isdigit() else "?"
    print(f"{name}  {kind}  {state}  age {elapsed}")
'''

TMUX_WAIT = r'''"""tmux_wait — block until a named signal arrives, instead of polling: exits
0 when it does, 124 if SECONDS pass first (default 60, cap 3600). Every job
started by tmux_start sends grim-JOB when it ends (a tmux hook does it, with
no help from the job); anything else can signal with
`tmux wait-for -S NAME`. A signal sent before the wait is remembered, and a
job that is already dead counts as signalled. Give `run` a timeout at least
as long as SECONDS, or the run is killed first.
Usage: tmux_wait SIGNAL [SECONDS]
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#wait-for"""
import os
import re
import shutil
import subprocess
import sys
import time

SESSION = "grim"
ROW = "#{@grim_job}\t#{pane_dead}\t#{pane_dead_status}"
DEFAULT_S, MAX_S = 60.0, 3600.0
TIMEOUT_EXIT_CODE = 124
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args, timeout=None):
    command = [*TMUX, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def job_state(signal):
    """How the job behind a grim-JOB signal is doing; '' if there is no such job."""
    listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
    rows = [line.split("\t") for line in listed.stdout.splitlines()]
    jobs = {row[0]: row[1:] for row in rows if len(row) == 3 and row[0]}
    job = signal.removeprefix("grim-")
    if not signal.startswith("grim-") or job not in jobs:
        return ""
    dead, status = jobs[job]
    if dead == "1":
        return f"job {job!r} is dead (exit {status})"
    return f"job {job!r} is still running"


def usage():
    print("usage: tmux_wait SIGNAL [SECONDS]   (SECONDS over 0, up to 3600)", file=sys.stderr)
    sys.exit(2)


if len(sys.argv) not in (2, 3) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", sys.argv[1]):
    usage()
signal = sys.argv[1]
try:
    seconds = float(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_S
except ValueError:
    usage()
if not 0 < seconds <= MAX_S:
    usage()
if shutil.which("tmux") is None:
    print("tmux_wait: program not found on PATH: 'tmux'", file=sys.stderr)
    sys.exit(127)

state = job_state(signal)
if "is dead" in state:
    print(f"{state}: nothing to wait for")
    sys.exit(0)
started = time.monotonic()
try:
    waited = tmux("wait-for", signal, timeout=seconds)
except subprocess.TimeoutExpired:
    note = job_state(signal) or "nothing has sent it"
    print(f"tmux_wait: no signal {signal!r} after {seconds:g}s — {note}", file=sys.stderr)
    sys.exit(TIMEOUT_EXIT_CODE)
if waited.returncode != 0:
    print(f"tmux_wait: tmux wait-for failed: {waited.stderr.strip()}", file=sys.stderr)
    sys.exit(1)
print(f"signal {signal!r} arrived after {time.monotonic() - started:.1f}s")
print(job_state(signal) or "no job is tied to this signal")
'''

# (name, description, body) rows — plain tuples, so this module never imports
# SeedSpec from bodies.py (which imports this one).
TMUX_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "tmux_start",
        "start a long-lived job (server, watcher, build, REPL) as a tmux window that "
        "outlives the run: tmux_start JOB PROGRAM [ARG...] — no shell",
        TMUX_START,
    ),
    (
        "tmux_list",
        "list the jobs started by tmux_start: name, kind, alive or dead with exit code, age",
        TMUX_LIST,
    ),
    (
        "tmux_wait",
        "block until a signal arrives instead of polling: tmux_wait grim-JOB [SECONDS] "
        "returns when that job ends (exit 124 if it has not)",
        TMUX_WAIT,
    ),
)
