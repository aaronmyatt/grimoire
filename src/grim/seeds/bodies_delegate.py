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

# (name, description, body) rows, as in bodies_tmux.py.
DELEGATE_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_done",
        "a delegate runs this when its answer is written: agent_done JOB checks result.md "
        "(headings, size) and only then marks the job DONE and signals grim-JOB",
        AGENT_DONE,
    ),
)
