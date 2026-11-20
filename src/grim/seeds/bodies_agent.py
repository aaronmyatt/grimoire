"""Seed bodies that drive another coding agent's TUI (claude, codex, pi) in a
tmux window — the delegate driver (docs/features/04-delegates.md). Data, not
code: each constant is one seed's full script text; bodies.py's SEEDS roster
wires AGENT_SEEDS in.

A delegate is a tmux job like any other (bodies_tmux.py), tagged
@grim_kind=agent, so tmux_list, tmux_wait, tmux_send and tmux_stop work on
it as they are. Only start and read are its own.
"""

from __future__ import annotations

AGENT_START = r'''"""agent_start — hand a task to another coding agent (claude, codex or pi)
running as a TUI in its own tmux window, in session "grim". The brief is read
from stdin and pasted in as ONE message once the TUI is ready, with a done
contract added: the delegate writes its answer to <run_dir>/JOB/result.md and
then signals grim-JOB. Known start-up dialogs are answered first (claude's
folder trust: yes; codex's update offer: skip).
Usage: agent_start AGENT JOB DIR   (brief on stdin)
Then: tmux_wait grim-JOB SECONDS blocks until it is done · agent_read JOB gets
the answer, or its state · tmux_send JOB TEXT follows up · tmux_stop JOB ends it.
A delegate's powers are not yours to set: extra launch flags (a permission
mode, say) and the program's path come only from [delegates.AGENT] in
~/.grimoire/config.toml (args = [...], bin = "..."); unset means its own defaults.
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#paste-buffer"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

SESSION = "grim"
MAX_BRIEF_CHARS = 20000
START_WAIT_S, POLL_S, KEY_GAP_S, PASTE_SETTLE_S = 90.0, 0.5, 0.4, 0.3
STABLE_POLLS = 4  # a screen unchanged for this many polls counts as ready
MAX_ANSWERS = 3  # per dialog, so one that never goes away cannot loop forever
# Start-up dialogs: (text only that dialog shows, the keys that answer it), and
# text an idle composer shows. Checked 2026-11-12: claude 2.1's folder-trust
# dialog defaults to "No, exit"; codex 0.167's update offer to "Update now".
PROFILES = {
    "claude": {
        "dialogs": [("yes, i trust this folder", ["Down", "Enter"])],
        "ready": ["? for shortcuts", "shift+tab to cycle", "auto mode on", "plan mode on"],
    },
    "codex": {
        "dialogs": [("skip until next version", ["Escape"])],
        "ready": ["ask codex to do anything", "? for shortcuts"],
    },
    "pi": {"dialogs": [], "ready": []},
}
LOGIN_HINTS = ("select login method", "paste your api key", "sign in with", "log in to")
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args, stdin=None, timeout=None):
    command = [*TMUX, *args]
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, check=False, timeout=timeout
    )


def fail(code, message):
    print(f"agent_start: {message}", file=sys.stderr)
    sys.exit(code)


def program_for(agent, config):
    """config's bin, else the first AGENT on PATH that is not a tmux-launching
    wrapper script (that would open the TUI in some other window, not this job's)."""
    if config.get("bin"):
        return str(config["bin"])
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / agent
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with open(candidate, "rb") as handle:
                head = handle.read(4096)
            if not (head.startswith(b"#!") and b"tmux new-" in head):
                return str(candidate)
    return None


if len(sys.argv) != 4:
    fail(2, "usage: agent_start AGENT JOB DIR   (brief on stdin)")
agent, job, folder = sys.argv[1:]
if agent not in PROFILES:
    fail(2, f"unknown agent {agent!r}; known: {', '.join(PROFILES)}")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
    fail(2, f"bad job name {job!r} (letters, digits, _ and - only)")
if not Path(folder).is_dir():
    fail(2, f"not a directory: {folder!r}")
brief = sys.stdin.read().strip()
if not 0 < len(brief) <= MAX_BRIEF_CHARS:
    fail(2, f"the brief goes on stdin, 1 to {MAX_BRIEF_CHARS} chars (got {len(brief)})")
home_config = Path.home() / ".grimoire" / "config.toml"
config_path = Path(os.environ.get("GRIM_DELEGATES_CONFIG") or home_config)
try:
    loaded = tomllib.loads(config_path.read_text()) if config_path.is_file() else {}
except (tomllib.TOMLDecodeError, OSError) as exc:
    fail(1, f"cannot read {config_path}: {exc}")
config = loaded.get("delegates", {}).get(agent, {})
extra = config.get("args", [])
if not isinstance(extra, list) or not all(isinstance(arg, str) for arg in extra):
    fail(1, f"[delegates.{agent}] args in {config_path} must be a list of strings")
program = program_for(agent, config)
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
if program is None or shutil.which(program) is None:
    fail(
        127,
        f"no {agent!r} program found on PATH (tmux wrapper scripts are skipped). "
        f'Set bin = "/path/to/{agent}" under [delegates.{agent}] in {config_path}',
    )

listed = tmux("list-windows", "-t", f"={SESSION}", "-F", "#{@grim_job}\t#{pane_dead}")
session_exists = listed.returncode == 0
states = dict(row.split("\t") for row in listed.stdout.splitlines() if row.count("\t") == 1)
if job in states:
    state = "dead" if states[job] == "1" else "live"
    fail(1, f"job {job!r} already exists ({state}) in tmux session {SESSION!r}; pick another name")
if session_exists:  # clear a wake left by an earlier job of this name (see tmux_start)
    tmux("wait-for", "-S", f"grim-{job}")
    try:
        tmux("wait-for", f"grim-{job}", timeout=1)
    except subprocess.TimeoutExpired:
        pass  # a live waiter took the signal instead; nothing is left over

run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
job_dir = run_dir / job
job_dir.mkdir(parents=True, exist_ok=True)
result_path, log_path = job_dir / "result.md", run_dir / f"{job}.log"
result_path.unlink(missing_ok=True)  # a result here must be THIS delegate's
log_path.write_text("")
message = (
    f"{brief}\n\nWhen done, write your answer to {result_path}\n"
    f"then run: tmux wait-for -S grim-{job}"
)
(job_dir / "brief.md").write_text(message + "\n")

# Open the window as tmux_start does: placeholder first, so tags, the log pipe
# and the job-end hook are in place before the TUI's first byte.
cwd = str(Path(folder).resolve())
create = ["new-window", "-t", f"={SESSION}:"] if session_exists else ["new-session", "-s", SESSION]
made = tmux(*create, "-d", "-n", job, "-c", cwd, "-P", "-F", "#{window_id}", "cat", "-")
if made.returncode != 0:
    fail(1, f"tmux could not open a window: {made.stderr.strip()}")
window = made.stdout.strip()
steps = [
    ["set-option", "-w", "-t", window, "remain-on-exit", "on"],
    ["set-option", "-w", "-t", window, "@grim_kind", "agent"],
    ["set-option", "-w", "-t", window, "@grim_job", job],
    ["set-option", "-w", "-t", window, "@grim_agent", agent],
    ["set-option", "-w", "-t", window, "@grim_started", str(int(time.time()))],
    ["pipe-pane", "-t", window, f"cat >> {shlex.quote(str(log_path))}"],
    ["set-hook", "-w", "-t", window, "pane-died", f"wait-for -S grim-{job}"],
    ["respawn-pane", "-k", "-t", window, "-c", cwd, "--", program, *extra],
]
for step in steps:
    done = tmux(*step)
    if done.returncode != 0:
        tmux("kill-window", "-t", window)
        fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")

# Wait for the composer: answer known dialogs, give up loudly on a login screen,
# an early exit, or the deadline. The brief is only sent to a ready TUI.
profile, answers, last, stable = PROFILES[agent], {}, None, 0
deadline = time.monotonic() + START_WAIT_S
while True:
    pane = tmux("display-message", "-p", "-t", window, "#{pane_dead}\t#{pane_dead_status}")
    dead, _, status = pane.stdout.strip().partition("\t")
    screen = tmux("capture-pane", "-p", "-t", window).stdout
    low = screen.lower()
    if dead == "1":
        fail(1, f"{agent} exited at once (exit {status}). Its last words: tmux_read {job}; "
                f"clear it with tmux_stop {job}")
    if any(hint in low for hint in LOGIN_HINTS):
        fail(1, f"{agent} wants a login, which a human must do: tmux attach -t {SESSION}")
    # TUIs collapse dialog labels against each other ("2.Skip3.Skipuntilnextversion"),
    # so hints are matched on text with every run of whitespace removed.
    flat = re.sub(r"\s+", "", low)
    dialog = next(
        (d for d in profile["dialogs"] if re.sub(r"\s+", "", d[0]) in flat), None
    )
    if dialog and answers.get(dialog[0], 0) < MAX_ANSWERS:
        answers[dialog[0]] = answers.get(dialog[0], 0) + 1
        for key in dialog[1]:
            tmux("send-keys", "-t", window, key)
            time.sleep(KEY_GAP_S)
        last, stable = None, 0
        continue
    stable = stable + 1 if screen.strip() and screen == last else 0
    last = screen
    if not dialog and (any(mark in low for mark in profile["ready"]) or stable >= STABLE_POLLS):
        break
    if time.monotonic() > deadline:
        fail(1, f"{agent} was not ready after {START_WAIT_S:g}s, so the brief was NOT sent. "
                f"See its screen: tmux_read {job}")
    time.sleep(POLL_S)

# One bracketed paste from a buffer filled over stdin, then Enter (see tmux_send).
buffer = f"grim-{job}"
sends = [
    (["load-buffer", "-b", buffer, "-"], message),
    (["paste-buffer", "-p", "-d", "-b", buffer, "-t", window], None),
    (["send-keys", "-t", window, "Enter"], None),
]
for step, stdin in sends:
    if step[0] == "send-keys":
        time.sleep(PASTE_SETTLE_S)
    done = tmux(*step, stdin=stdin)
    if done.returncode != 0:
        fail(1, f"tmux {step[0]} failed: {done.stderr.strip()}")
print(f"started {SESSION}:{job} ({agent}) in {cwd}")
print(f"brief: {job_dir / 'brief.md'}")
print(f"result: {result_path}")
print(f"next: tmux_wait grim-{job} SECONDS, then agent_read {job}")
'''

AGENT_READ = r'''"""agent_read — where a delegate stands, and what it has to say. The first
line names its state:
  DONE         it wrote result.md: that text follows (its answer, not a screen scrape)
  WORKING      still busy: the tail of its screen follows
  NEEDS-INPUT  it is asking something it cannot get past: the question follows.
               Answer with tmux_send JOB TEXT, or a human types in its pane
  IDLE         it stopped with no result.md: its screen follows
  EXITED       its program ended with no result.md: the tail of its log follows
Usage: agent_read JOB [LINES]   (LINES of screen or log to show: default 40, cap 400)
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#capture-pane"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SESSION = "grim"
ROW = "#{@grim_job}\t#{window_id}\t#{pane_dead}\t#{pane_dead_status}\t#{@grim_kind}"
DEFAULT_LINES, MAX_LINES = 40, 400
WORKING_MARKS = ("esc to interrupt", "ctrl+c to interrupt", "esc to stop")
ASKING_MARKS = (
    "enter to confirm", "(y/n)", "[y/n]", "yes, i trust this folder",
    "skip until next version", "select login method", "paste your api key", "sign in with",
)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")  # CSI and OSC escapes
USAGE = f"usage: agent_read JOB [LINES]   (LINES 1-{MAX_LINES})"
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args):
    return subprocess.run([*TMUX, *args], capture_output=True, text=True, check=False)


def fail(code, message):
    print(f"agent_read: {message}", file=sys.stderr)
    sys.exit(code)


if not 2 <= len(sys.argv) <= 3 or not all(number.isdigit() for number in sys.argv[2:]):
    fail(2, USAGE)
job = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_LINES
if not 1 <= count <= MAX_LINES:
    fail(2, USAGE)
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = {row[0]: row[1:] for row in rows if len(row) == 5 and row[0]}
if job not in jobs:
    fail(1, f"no such job: {job!r} (jobs: {', '.join(sorted(jobs)) or 'none'})")
window, dead, status, kind = jobs[job]
if kind != "agent":
    fail(1, f"job {job!r} is not a delegate (kind {kind!r}); read it with tmux_read {job}")
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
result_path = run_dir / job / "result.md"
if result_path.is_file():
    print(f"[agent_read] {job}: DONE · result.md follows ({result_path})")
    print(result_path.read_text(errors="replace").rstrip("\n"))
    sys.exit(0)
if dead == "1":
    log_path = run_dir / f"{job}.log"
    text = ANSI.sub("", log_path.read_text(errors="replace")) if log_path.is_file() else ""
    state, source = f"EXITED (exit {status})", "the tail of its log"
else:
    shot = tmux("capture-pane", "-p", "-J", "-t", window)
    if shot.returncode != 0:
        fail(1, f"tmux capture-pane failed: {shot.stderr.strip()}")
    text, low, source = shot.stdout, shot.stdout.lower(), "its screen"
    if any(mark in low for mark in WORKING_MARKS):
        state = "WORKING"
    elif any(mark in low for mark in ASKING_MARKS):
        state = "NEEDS-INPUT"
    else:
        state = "IDLE"
lines = [line.rstrip() for line in text.splitlines() if line.strip()][-count:]
print(f"[agent_read] {job}: {state} · no result.md · {source} follows")
print("\n".join(lines))
'''

# (name, description, body) rows, as in bodies_tmux.py.
AGENT_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_start",
        "delegate a task to another coding agent (claude, codex, pi) in a tmux window: "
        "agent_start AGENT JOB DIR with the brief on stdin; it answers in result.md",
        AGENT_START,
    ),
    (
        "agent_read",
        "read a delegate started by agent_start: its state (DONE, WORKING, NEEDS-INPUT, "
        "IDLE, EXITED) and its answer from result.md, or its screen: agent_read JOB",
        AGENT_READ,
    ),
)
