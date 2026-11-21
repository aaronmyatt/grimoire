"""The tmux_read seed body — moved verbatim out of bodies_tmux.py so that
module stays under the 500-line file budget (docs/delegation-plan.md step
1a). Data, not code: bodies_tmux.py's TMUX_SEEDS roster imports it.
Ref: https://man.openbsd.org/tmux#capture-pane
"""

from __future__ import annotations

TMUX_READ = r'''"""tmux_read — read a job's output: the last LINES lines (default 200, cap
2000) of its screen and scrollback while it is alive, or of its log file once
it is dead (a dead pane's screen is blank; the log keeps its last words and
tmux keeps its exit code). The first line printed is a status line: alive or
dead (exit N), which lines you got, how to page back, and where the full log
is (read_file can slice it). The cost is bounded at any size: only the last
4 MiB of a log and the last LINES+SKIP lines of scrollback are read, and the
status line says `truncated` when that bound cut something off.
Usage: tmux_read JOB [LINES] [SKIP]   (SKIP = newest lines to skip: paging back)
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#capture-pane"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SESSION = "grim"
ROW = "#{@grim_job}\t#{window_id}\t#{pane_dead}\t#{pane_dead_status}\t#{history_size}"
DEFAULT_LINES, MAX_LINES = 200, 2000
LOG_TAIL_BYTES = 4 * 1024 * 1024  # a dead job's log is read from at most this far back
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")  # CSI and OSC escapes
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args):
    return subprocess.run([*TMUX, *args], capture_output=True, text=True, check=False)


def log_tail(path):
    """(text, truncated): the log's last LOG_TAIL_BYTES, cut at a line start.
    Ref: https://docs.python.org/3/library/io.html#io.IOBase.seek"""
    if not path.is_file():
        return "", False
    with path.open("rb") as log:
        size = log.seek(0, os.SEEK_END)
        log.seek(max(0, size - LOG_TAIL_BYTES))
        data = log.read(LOG_TAIL_BYTES)
    cut = size > LOG_TAIL_BYTES
    if cut:  # the first line was cut part-way: drop it
        data = data[data.find(b"\n") + 1 :]
    return ANSI.sub("", data.decode(errors="replace")), cut


def fail(code, message):
    print(f"tmux_read: {message}", file=sys.stderr)
    sys.exit(code)


numbers = sys.argv[2:]
if not 2 <= len(sys.argv) <= 4 or not all(number.isdigit() for number in numbers):
    fail(2, f"usage: tmux_read JOB [LINES] [SKIP]   (LINES 1-{MAX_LINES}, SKIP 0 or more)")
job = sys.argv[1]
count = int(numbers[0]) if numbers else DEFAULT_LINES
skip = int(numbers[1]) if len(numbers) == 2 else 0
if not 1 <= count <= MAX_LINES:
    fail(2, f"usage: tmux_read JOB [LINES] [SKIP]   (LINES 1-{MAX_LINES}, SKIP 0 or more)")
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = {row[0]: row[1:] for row in rows if len(row) == 5 and row[0]}
if job not in jobs:
    fail(1, f"no such job: {job!r} (jobs: {', '.join(sorted(jobs)) or 'none'})")
window, dead, status, history = jobs[job]
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
log_path = run_dir / f"{job}.log"
if dead == "1":
    text, truncated = log_tail(log_path)
    state, source = f"dead (exit {status})", "its log"
else:
    # -S -N / -E - : from N lines up the scrollback to the bottom of the screen,
    # never the whole history; -J joins lines the terminal wrapped.
    back = count + skip
    shot = tmux("capture-pane", "-p", "-J", "-t", window, "-S", f"-{back}", "-E", "-")
    if shot.returncode != 0:
        fail(1, f"tmux capture-pane failed: {shot.stderr.strip()}")
    text, state, source = shot.stdout, "alive", "its screen"
    truncated = history.isdigit() and int(history) > back
lines = text.splitlines()
while lines and not lines[-1].strip():
    lines.pop()
end = max(0, len(lines) - skip)
start = max(0, end - count)
span = f"lines {start + 1}-{end} of {len(lines)}" if end > start else f"no lines of {len(lines)}"
older = f" · older: tmux_read {job} {count} {skip + count}" if start > 0 or truncated else ""
bound = " · truncated" if truncated else ""
print(f"[tmux_read] {job}: {state} · {span} from {source}{bound}{older} · full log: {log_path}")
if end > start:
    print("\n".join(lines[start:end]))
'''
