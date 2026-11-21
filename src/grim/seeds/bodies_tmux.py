"""Seed bodies that drive tmux — the first surface driver (docs/surfaces.md,
docs/features/01-jobs.md). Data, not code: each constant is one seed's full
script text; bodies.py's SEEDS roster wires TMUX_SEEDS in.

A job is a tmux window in session "grim", tagged with window options
(@grim_kind, @grim_job, @grim_started). tmux itself is the registry: no pid
file, nothing to drift. The job's program is exec'd as argv — never through a
shell, same rule as bodies_exec.py. Ref: https://man.openbsd.org/tmux
"""

from __future__ import annotations

from grim.seeds.bodies_tmux_read import TMUX_READ

TMUX_START = r'''"""tmux_start — start a long-lived job as a tmux window in session "grim":
servers, watchers, builds, REPLs — work a normal `grim run` would kill at its
timeout. The window IS the job and tmux is the only registry (no pid file).
Usage: tmux_start [--kind agent] JOB PROGRAM [ARG...]   (a single quoted
command string is split shlex-style; --kind tags the window, default job).
No shell: pipes, redirects, && and shell interpreters are refused — put that
logic in a python script: tmux_start JOB grim run SCRIPT.
The job runs in your cwd; its output is logged to <run_dir>/JOB.log
($GRIM_RUN_DIR or ~/.grimoire/run). See jobs with tmux_list; a human watches
with `tmux attach -t grim`. When the job ends, tmux itself signals grim-JOB:
block on that with tmux_wait grim-JOB instead of polling. Two starts of one
name at once make one window: the other exits 1, "name is being started".
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#respawn-pane"""
import base64
import fcntl
import json
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
KINDS = ("job", "agent")  # @grim_kind values; agent_ask opens its delegates with agent
# tmux re-parses respawn-pane's argv: an argument ending in ";" is cut there as a
# command separator, and one lone argument runs under `sh -c`. So the job's argv
# rides as base64 JSON into this fixed stub, which execs it unchanged.
# Ref: https://docs.python.org/3/library/os.html#os.execvp
EXEC_STUB = (
    "import base64, json, os, sys\n"
    "argv = json.loads(base64.b64decode(sys.argv[1]))\n"
    "os.execvp(argv[0], argv)\n"
)


def tmux(*args, timeout=None):
    socket = os.environ.get("GRIM_TMUX_SOCKET")
    base = ["tmux", "-L", socket] if socket else ["tmux"]
    command = [*base, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def fail(code, message):
    print(f"tmux_start: {message}", file=sys.stderr)
    sys.exit(code)


args, kind = sys.argv[1:], "job"
if args[:1] == ["--kind"]:
    kind, args = (args[1:2] or [""])[0], args[2:]
    if not re.fullmatch(r"[a-z]{1,16}", kind) or kind not in KINDS:
        fail(2, f"unknown --kind {kind!r}; known: {', '.join(KINDS)}")
if len(args) < 2:
    fail(2, "usage: tmux_start [--kind agent] JOB PROGRAM [ARG...]")
job = args[0]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
    fail(2, f"bad job name {job!r} (letters, digits, _ and - only)")
# A sole token that is an executable file is a path (spaces and all), never split.
sole = len(args) == 2 and os.path.isfile(args[1]) and os.access(args[1], os.X_OK)
argv = [args[1]] if sole else shlex.split(args[1]) if len(args) == 2 else args[1:]
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

run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
run_dir.mkdir(parents=True, exist_ok=True)
# One start of a name at a time: a non-blocking lock, held from the lookup below
# to the last tag, so parallel starts of one name open one window. The lock file
# is per server and name (another name never waits); the kernel frees the lock
# if this process dies. Ref: https://docs.python.org/3/library/fcntl.html#fcntl.flock
server = re.sub(r"[^A-Za-z0-9_-]", "_", os.environ.get("GRIM_TMUX_SOCKET") or "default")
guard = (run_dir / f".tmux-start-{server}-{job}.lock").open("a")
try:
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    fail(1, "name is being started")

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
    ["set-option", "-w", "-t", window, "@grim_kind", kind],
    ["set-option", "-w", "-t", window, "@grim_job", job],
    ["set-option", "-w", "-t", window, "@grim_started", str(int(time.time()))],
    ["pipe-pane", "-t", window, f"cat >> {shlex.quote(str(log_path))}"],
    ["set-hook", "-w", "-t", window, "pane-died", f"wait-for -S grim-{job}"],
    ["respawn-pane", "-k", "-t", window, "-c", cwd, "--", sys.executable, "-c", EXEC_STUB,
     base64.b64encode(json.dumps(argv).encode()).decode()],
]
for step in steps:
    done = tmux(*step)
    if done.returncode != 0:
        tmux("kill-window", "-t", window)
        fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")
guard.close()  # the last tag is set: the name is taken in tmux, so the lock can go
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
job already over counts as signalled. Exit 3, not 0, when the job behind
grim-JOB ended badly: a non-zero exit, a delegate with no answer agent_done
checked, or a tmux_stop before it finished. Give `run` a timeout at least as
long as SECONDS, or the run is killed first.
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
ROW = "#{@grim_job}\t#{pane_dead}\t#{pane_dead_status}\t#{@grim_kind}\t#{@grim_done}"
DEFAULT_S, MAX_S = 60.0, 3600.0
TIMEOUT_EXIT_CODE, BAD_END_EXIT_CODE = 124, 3
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args, timeout=None):
    command = [*TMUX, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def job_row(signal):
    """(job, [dead, exit, kind, done stamp]) behind a grim-JOB signal; None if no such job."""
    listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
    rows = [line.split("\t") for line in listed.stdout.splitlines()]
    jobs = {row[0]: row[1:] for row in rows if len(row) == 5 and row[0]}
    job = signal.removeprefix("grim-")
    if not signal.startswith("grim-") or job not in jobs:
        return None
    return job, jobs[job]


def verdict(found):
    """(exit code, what to say) once the job is over; None while it still runs."""
    job, (dead, status, kind, stamp) = found
    if stamp:  # agent_done passed the delegate's answer
        return 0, f"job {job!r} is done (agent_done checked its answer)"
    if dead != "1":
        return None
    if kind == "agent":
        return BAD_END_EXIT_CODE, f"job {job!r} EXITED (exit {status}): no result"
    return (0 if status == "0" else BAD_END_EXIT_CODE), f"job {job!r} is dead (exit {status})"


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

before = job_row(signal)
over = verdict(before) if before else None
if over:
    print(f"{over[1]}: nothing to wait for")
    sys.exit(over[0])
started = time.monotonic()
try:
    waited = tmux("wait-for", signal, timeout=seconds)
except subprocess.TimeoutExpired:
    found = job_row(signal)
    note = f"job {found[0]!r} is still running" if found else "nothing has sent it"
    print(f"tmux_wait: no signal {signal!r} after {seconds:g}s — {note}", file=sys.stderr)
    sys.exit(TIMEOUT_EXIT_CODE)
if waited.returncode != 0:
    print(f"tmux_wait: tmux wait-for failed: {waited.stderr.strip()}", file=sys.stderr)
    sys.exit(1)
print(f"signal {signal!r} arrived after {time.monotonic() - started:.1f}s")
after = job_row(signal)
if before and not after:
    print(f"job {before[0]!r} was stopped before it finished")
    sys.exit(BAD_END_EXIT_CODE)
over = verdict(after) if after else None
if over:
    print(over[1])
    sys.exit(over[0])
print(f"job {after[0]!r} is still running" if after else "no job is tied to this signal")
'''

TMUX_SEND = r'''"""tmux_send — type one line into a live job, then press Enter: answer a
prompt, drive a REPL, talk to a TUI. TEXT is typed exactly as given — words
like Enter or C-c in it are just letters, never tmux key names — so pass it as
ONE argument. Long or multi-line TEXT goes in as a single bracketed paste, so a
TUI (another agent, say) gets one message, not one per line. Read the reply
with tmux_read JOB.
Usage: tmux_send JOB TEXT
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#send-keys"""
import os
import shutil
import subprocess
import sys
import time

SESSION = "grim"
ROW = "#{@grim_job}\t#{window_id}\t#{pane_dead}\t#{pane_dead_status}"
PASTE_OVER = 80  # chars: longer (or multi-line) text is pasted, not typed
PASTE_SETTLE_S = 0.3  # a TUI needs a beat to take a paste in before Enter
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args, stdin=None):
    command = [*TMUX, *args]
    return subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)


def fail(code, message):
    print(f"tmux_send: {message}", file=sys.stderr)
    sys.exit(code)


if len(sys.argv) != 3:
    fail(2, "usage: tmux_send JOB TEXT   (TEXT is one argument; Enter is pressed after it)")
job, text = sys.argv[1], sys.argv[2]
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = {row[0]: row[1:] for row in rows if len(row) == 4 and row[0]}
if job not in jobs:
    fail(1, f"no such job: {job!r} (jobs: {', '.join(sorted(jobs)) or 'none'})")
window, dead, status = jobs[job]
if dead == "1":
    fail(1, f"job {job!r} is dead (exit {status}): nothing can be typed. See tmux_read {job}")
pasted = len(text) > PASTE_OVER or "\n" in text
if pasted:
    # The buffer is filled from stdin (so the text is never an argv) and pasted
    # with -p: bracketed, when the program asked for that, so it arrives whole.
    # Ref: https://man.openbsd.org/tmux#paste-buffer
    buffer = f"grim-{job}"
    steps = [(["load-buffer", "-b", buffer, "-"], text),
             (["paste-buffer", "-p", "-d", "-b", buffer, "-t", window], None)]
else:
    # -l types the text literally; "--" keeps a TEXT that starts with "-" from
    # being read as a flag.
    steps = [(["send-keys", "-t", window, "-l", "--", text], None)]
for step, stdin in steps:
    done = tmux(*step, stdin=stdin)
    if done.returncode != 0:
        fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")
if pasted:
    time.sleep(PASTE_SETTLE_S)
entered = tmux("send-keys", "-t", window, "Enter")
if entered.returncode != 0:
    fail(1, f"tmux send-keys failed: {entered.stderr.strip()}")
how = f"pasted {len(text.splitlines())} lines" if pasted else f"typed {text!r}"
print(f"{how} into {SESSION}:{job}, then Enter")
'''

TMUX_STOP = r'''"""tmux_stop — stop a job and free its name: its tmux window is closed, live
or dead (a live program gets SIGHUP), and anything blocked in
tmux_wait grim-JOB is woken. The job's log file is kept as its record.
Usage: tmux_stop JOB
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#kill-window"""
import os
import shutil
import subprocess
import sys

SESSION = "grim"
ROW = "#{@grim_job}\t#{window_id}\t#{pane_dead}\t#{pane_dead_status}"
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args):
    return subprocess.run([*TMUX, *args], capture_output=True, text=True, check=False)


def fail(code, message):
    print(f"tmux_stop: {message}", file=sys.stderr)
    sys.exit(code)


if len(sys.argv) != 2:
    fail(2, "usage: tmux_stop JOB")
job = sys.argv[1]
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = {row[0]: row[1:] for row in rows if len(row) == 4 and row[0]}
if job not in jobs:
    fail(1, f"no such job: {job!r} (jobs: {', '.join(sorted(jobs)) or 'none'})")
window, dead, status = jobs[job]
# One tmux call does both: close the window, then wake any waiter by hand —
# kill-window fires no pane-died hook. With no waiter the wake is left
# behind; the next tmux_start of this name clears it.
stopped = tmux("kill-window", "-t", window, ";", "wait-for", "-S", f"grim-{job}")
if stopped.returncode != 0:
    fail(1, f"tmux kill-window failed: {stopped.stderr.strip()}")
was = f"dead (exit {status})" if dead == "1" else "alive"
print(f"stopped {SESSION}:{job} (it was {was})")
'''

# (name, description, body) rows — plain tuples, so this module never imports
# SeedSpec from bodies.py (which imports this one).
TMUX_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "tmux_start",
        "start a long-lived job in the background (dev server, watcher, build, REPL) as a "
        "tmux window that outlives the run: tmux_start [--kind agent] JOB PROGRAM [ARG...] — no shell",
        TMUX_START,
    ),
    (
        "tmux_list",
        "what is running? list the jobs started by tmux_start: name, kind, alive or dead "
        "with exit code, age",
        TMUX_LIST,
    ),
    (
        "tmux_read",
        "read or tail a job's output: its last lines from the live screen, or from its log with the "
        "exit code once it is dead: tmux_read JOB [LINES] [SKIP]",
        TMUX_READ,
    ),
    (
        "tmux_send",
        "type one line into a live job and press Enter (a prompt, a REPL, a TUI): "
        "tmux_send JOB TEXT — typed as-is",
        TMUX_SEND,
    ),
    (
        "tmux_stop",
        "stop or kill a job started by tmux_start and free its name: tmux_stop JOB — closes its "
        "tmux window, keeps its log",
        TMUX_STOP,
    ),
    (
        "tmux_wait",
        "wait for a job (a build, a server) to end — is it done? blocks on its signal, no "
        "polling: tmux_wait grim-JOB [SECONDS] (exit 3 if it ended badly, 124 if it has not)",
        TMUX_WAIT,
    ),
)
