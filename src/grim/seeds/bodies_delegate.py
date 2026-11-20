"""Seed bodies that make a delegate's "done" a checked fact (docs/delegation-plan.md).
Data, not code: bodies.py's SEEDS roster wires DELEGATE_SEEDS in."""

from __future__ import annotations

AGENT_DONE = r'''"""agent_done — a delegate runs this once <run_dir>/JOB/result.md is
written. Only if the file passes (UTF-8, 200-16384 bytes, every heading below
with text under it, a numbered claim under ## Claims) does it write status.json
(with the file's sha256), stamp the window @grim_done and signal grim-JOB.
Otherwise it exits 1, marks nothing, and lists every problem on stderr.
Usage: agent_done JOB
$GRIM_TMUX_SOCKET names a private tmux server.
Ref: https://man.openbsd.org/tmux#wait-for"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SESSION = "grim"
ROW = "#{@grim_job}\t#{window_id}\t#{@grim_kind}"
MIN_BYTES, MAX_BYTES = 200, 16384
HEADINGS = ("## Verdict", "## Answer", "## Claims", "## Confidence", "## Open")
CLAIM = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)  # "1. text" or "1) text"
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]


def tmux(*args):
    return subprocess.run([*TMUX, *args], capture_output=True, text=True, check=False)


def fail(code, message):
    print(f"agent_done: {message}", file=sys.stderr)
    sys.exit(code)


def problems(raw):
    """Every reason the answer does not pass yet; [] when it does."""
    assert isinstance(raw, bytes), "the answer is checked as raw bytes"
    if not MIN_BYTES <= len(raw) <= MAX_BYTES:
        return [f"result.md is {len(raw)} bytes; it must be {MIN_BYTES} to {MAX_BYTES}"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"result.md is not UTF-8: {exc}"]
    found, current = {}, None  # heading -> the text under it
    for line in text.splitlines():
        current = line.rstrip() if line.rstrip() in HEADINGS else current
        found[current] = found.get(current, "") + ("" if line.rstrip() == current else line + "\n")
    assert set(found) <= {None, *HEADINGS}, "only required headings are collected"
    errors = [f"missing heading: {h}" for h in HEADINGS if h not in found]
    errors += [f"nothing under: {h}" for h in HEADINGS if h in found and not found[h].strip()]
    if found.get("## Claims", "").strip() and not CLAIM.search(found["## Claims"]):
        errors.append("## Claims needs at least one numbered claim: 1. claim — evidence")
    return errors


if len(sys.argv) != 2:
    fail(2, "usage: agent_done JOB")
job = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
    fail(2, f"bad job name {job!r} (letters, digits, _ and - only)")
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")
listed = tmux("list-windows", "-t", f"={SESSION}", "-F", ROW)
rows = [line.split("\t") for line in listed.stdout.splitlines()]
jobs = {row[0]: row[1:] for row in rows if len(row) == 3 and row[0]}
if job not in jobs:
    fail(1, f"no such job: {job!r} (jobs: {', '.join(sorted(jobs)) or 'none'})")
window, kind = jobs[job]
if kind != "agent":
    fail(1, f"job {job!r} is not a delegate (kind {kind!r})")
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
job_dir, result_path = run_dir / job, run_dir / job / "result.md"
if not result_path.is_file():
    fail(1, f"no answer yet: write it to {result_path}, then run agent_done {job} again")
with result_path.open("rb") as handle:
    raw = handle.read(MAX_BYTES + 1)  # bounded: one byte over the cap is enough to refuse
errors = problems(raw)
if errors:
    fail(1, " · ".join(errors) + f". Required headings: {' '.join(HEADINGS)}")

# status.json by atomic rename, then stamp + wake in ONE tmux call. Ref: https://docs.python.org/3/library/os.html#os.replace
sha, now = hashlib.sha256(raw).hexdigest(), int(time.time())
status = {"job": job, "state": "DONE", "bytes": len(raw), "sha256": sha, "stamped_at": now}
partial = job_dir / "status.json.tmp"
partial.write_text(json.dumps(status) + "\n")
os.replace(partial, job_dir / "status.json")
stamp = f"{now}:{len(raw)}:{sha[:8]}"
marked = tmux("set-option", "-w", "-t", window, "@grim_done", stamp, ";",
              "wait-for", "-S", f"grim-{job}")
if marked.returncode != 0:
    fail(1, f"status.json is written but tmux could not stamp: {marked.stderr.strip()}")
print(f"done {job} ({len(raw)} bytes)")
'''

AGENT_WAIT = r'''"""agent_wait — block ONCE until every job named is over, or SECONDS pass.
No polling: one `tmux wait-for grim-JOB` per job still running, all watched
together. Durable state is checked before and after every wake, so a wake
someone else took is never missed: <run_dir>/JOB/status.json (written by
agent_done or agent_exec) wins; else a dead pane (a delegate with no answer:
EXITED; a plain job: its exit code); else a window gone (STOPPED).
One line per job; exit 0 all DONE (a plain job: exit 0), 3 all over and any
failed, 124 any still running. Give `run` a timeout a little over SECONDS.
Usage: agent_wait SECONDS JOB [JOB...]   (SECONDS up to 3500, up to 4 JOBs)
Ref: https://docs.python.org/3/library/selectors.html"""
import hashlib
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

SESSION = "grim"
ROW = "#{@grim_job}\t#{pane_dead}\t#{pane_dead_status}\t#{@grim_kind}"
MAX_JOBS, MAX_S, MAX_REARMS = 4, 3500.0, 3
TIMEOUT_EXIT_CODE, BAD_END_EXIT_CODE = 124, 3
socket = os.environ.get("GRIM_TMUX_SOCKET")
TMUX = ["tmux", "-L", socket] if socket else ["tmux"]
RUN_DIR = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")


def fail(code, message):
    print(f"agent_wait: {message}", file=sys.stderr)
    sys.exit(code)


def durable(job):
    """status.json's state; DONE only while result.md still has the sha agent_done
    stamped (as agent_read checks). None when there is no record."""
    try:
        record = json.loads((RUN_DIR / job / "status.json").read_text())
    except (OSError, ValueError):
        return None
    state = record.get("state") if isinstance(record, dict) else None
    if state == "DONE":
        try:
            digest = hashlib.sha256((RUN_DIR / job / "result.md").read_bytes()).hexdigest()
        except OSError:
            digest = None
        state = "DONE" if record.get("sha256") == digest else "CHANGED"
    return (str(state), record) if state else None


def states(jobs, seen):
    """job -> (state, ok, detail) once over, None while running. One list-windows
    call; the same rules as tmux_wait. seen: jobs that had a window at any check."""
    listed = subprocess.run([*TMUX, "list-windows", "-t", f"={SESSION}", "-F", ROW],
                            capture_output=True, text=True, check=False)
    rows = [line.split("\t") for line in listed.stdout.splitlines()]
    windows = {row[0]: row[1:] for row in rows if len(row) == 4 and row[0]}
    seen.update(set(jobs) & set(windows))
    found = {}
    for job in jobs:
        record, (dead, status, kind) = durable(job), windows.get(job, ("", "", ""))
        if record:
            found[job] = (record[0], record[0] == "DONE", record[1])
        elif job not in windows:
            found[job] = ("STOPPED" if job in seen else "GONE", False, {})
        elif dead == "1" and kind == "agent":
            found[job] = ("EXITED", False, {"exit_code": status})
        elif dead == "1":  # a plain job: its exit code decides
            ok = status == "0"
            found[job] = ("DONE" if ok else "FAILED", ok, {"exit_code": status})
        else:
            found[job] = None
    assert set(found) == set(jobs), "every job has a verdict or None"
    return found


def line(job, verdict):
    """One line: JOB STATE, then wall/cost/size for an answer or the exit code."""
    if verdict is None:
        return f"{job} RUNNING"
    state, _ok, record = verdict
    try:
        meta = json.loads((RUN_DIR / job / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    wall, cost = meta.get("wall_s"), meta.get("cost_usd")
    parts = [job, state]
    parts += [f"{int(wall) // 60}m{int(wall) % 60:02d}s"] if isinstance(wall, (int, float)) else []
    parts += [f"${cost:.2f}" if isinstance(cost, (int, float)) else "cost=null"] if meta else []
    parts += [f"{record['bytes'] / 1024:.1f}KiB"] if isinstance(record.get("bytes"), int) else []
    code = record.get("exit_code")
    parts += [f"exit {code}"] if code not in (None, "0", 0) else []
    return " ".join(parts)


args = sys.argv[1:]
try:
    seconds = float(args[0]) if args else -1.0
except ValueError:
    seconds = -1.0
jobs = list(dict.fromkeys(args[1:]))
named = all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job) for job in jobs)
if not 0 < seconds <= MAX_S or not 1 <= len(jobs) <= MAX_JOBS or not named:
    fail(2, f"usage: agent_wait SECONDS JOB [JOB...]   (SECONDS up to {MAX_S:g}, "
         f"1 to {MAX_JOBS} distinct JOBs)")
if shutil.which("tmux") is None:
    fail(127, "program not found on PATH: 'tmux'")

started, seen = time.monotonic(), set()
deadline, arms, waiters = started + seconds, dict.fromkeys(jobs, 0), {}
selector = selectors.DefaultSelector()
found = states(jobs, seen)
try:
    for _round in range(MAX_JOBS * (MAX_REARMS + 1) + 1):  # each round spends one arm
        remaining = deadline - time.monotonic()
        pending = [job for job in jobs if found[job] is None]
        if not pending or remaining <= 0:
            break
        for job in pending:
            if job not in waiters and arms[job] <= MAX_REARMS:
                arms[job] += 1
                waiter = subprocess.Popen([*TMUX, "wait-for", f"grim-{job}"],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                waiters[job] = waiter
                selector.register(waiter.stdout, selectors.EVENT_READ, job)
        found = states(jobs, seen)  # again: a wake taken before our arm is not missed
        if all(found[job] is not None for job in jobs) or not waiters:
            break
        for key, _mask in selector.select(timeout=remaining):  # EOF: that client woke
            selector.unregister(key.fileobj)
            waiters.pop(key.data).wait()
        found = states(jobs, seen)
finally:  # never leave a wait-for client behind
    for waiter in waiters.values():
        waiter.kill()
        waiter.wait()
for job in jobs:
    print(line(job, found[job]))
print(f"waited {time.monotonic() - started:.1f}s")
if any(found[job] is None for job in jobs):
    sys.exit(TIMEOUT_EXIT_CODE)
sys.exit(0 if all(found[job][1] for job in jobs) else BAD_END_EXIT_CODE)
'''

# (name, description, body) rows, as in bodies_tmux.py.
DELEGATE_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_done",
        "a delegate runs this when its answer is written: agent_done JOB checks result.md "
        "(headings, size) and only then marks the job DONE and signals grim-JOB",
        AGENT_DONE,
    ),
    (
        "agent_wait",
        "block once until every job named is over or SECONDS pass, no polling: "
        "agent_wait SECONDS JOB [JOB...] — 0 all DONE, 3 any failed, 124 still running",
        AGENT_WAIT,
    ),
)
