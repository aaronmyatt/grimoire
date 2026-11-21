"""Seed body that arbitrates two delegates' answers mechanically, with no model
(docs/delegation-plan.md, Phase 3b; packet shape in §5), and agent_stats, the scoreboard
of past asks (6c). Data, not code: bodies.py's SEEDS roster wires COMPARE_SEEDS in."""

from __future__ import annotations

AGENT_COMPARE = r'''"""agent_compare — one packet (at most 4096 bytes) comparing two delegates'
answers, with no model in the loop. State comes from <run_dir>/JOB/status.json
only (DONE needs agent_done's sha to still match result.md). Each DONE
result.md is read by heading. Each claim's evidence is checked: path:line must
name a file inside the delegate's folder (meta.json "dir") with at least that
many lines; a `command` is recorded, never run. Claims match when their words
overlap (Jaccard >= 0.5, after lowercasing, dropping punctuation and a fixed
stopword list); so do the verdicts: AGREE, else DIFFER.
Marks: + evidence checked, x evidence wrong, ? not checkable (a command, no path).
Exit 0 both DONE, 3 only one (single source), 1 neither.
Usage: agent_compare JOB_A JOB_B
Ref: https://en.wikipedia.org/wiki/Jaccard_index"""
import hashlib
import json
import os
import re
import string
import sys
from pathlib import Path

MAX_PACKET_BYTES, MAX_CLAIM_CHARS, MAX_LISTED, SAME = 4096, 160, 8, 0.5
MAX_RESULT_BYTES, MAX_SCAN_BYTES = 16384, 4 << 20  # result.md cap; most of a file line-counted
HEADINGS = ("## Verdict", "## Answer", "## Claims", "## Confidence", "## Open")
STOPWORDS = frozenset(
    "a an and are as at be but by for from has have in is it its of on or that the "
    "their there this to was were will with which not no can should does do so than".split()
)
PATH_LINE = re.compile(r"([\w./~-]+\.\w+):(\d+)")  # src/app.py:12 — a name with an extension
RUN_DIR = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")


def fail(code, message):
    print(f"agent_compare: {message}", file=sys.stderr)
    sys.exit(code)


def load(job):
    """(state, sections, meta). State from status.json alone; MALFORMED when a DONE
    result.md lacks a heading. sections: heading -> text, {} unless DONE."""
    folder = RUN_DIR / job
    try:
        record = json.loads((folder / "status.json").read_text())
    except (OSError, ValueError):
        record = {}
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    state = str(record.get("state", "NO STATUS")) if isinstance(record, dict) else "NO STATUS"
    try:
        raw = (folder / "result.md").read_bytes()[:MAX_RESULT_BYTES + 1]
    except OSError:
        raw = b""
    if state == "DONE" and record.get("sha256") != hashlib.sha256(raw).hexdigest():
        state = "CHANGED"
    sections, current = {}, None
    for line in raw.decode("utf-8", "replace").splitlines() if state == "DONE" else []:
        current = line.rstrip() if line.rstrip() in HEADINGS else current
        if current and line.rstrip() != current:
            sections[current] = sections.get(current, "") + line + "\n"
    if state == "DONE" and set(HEADINGS) - set(sections):
        state = "MALFORMED"
    assert state != "DONE" or sections, "a DONE answer has its sections"
    return state, (sections if state == "DONE" else {}), (meta if isinstance(meta, dict) else {})


def lines_in(path):
    """How many lines a file has (bounded read), or -1 when it cannot be read."""
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_SCAN_BYTES).count(b"\n") + 1
    except OSError:
        return -1


def mark(claim, folder):
    """+ every path:line checks out, x one does not, ? nothing checkable."""
    refs = PATH_LINE.findall(claim)
    if not refs or not folder:
        return "?"
    root = Path(folder).resolve()
    for name, line in refs:
        target = (root / name).resolve()
        if not target.is_relative_to(root) or lines_in(target) < int(line):
            return "x"
    return "+"


def words(text):
    """The claim's words that carry meaning: the text before any evidence."""
    head = re.split(r"(?i)\bevidence\b", text)[0].lower()
    cleaned = head.translate(str.maketrans("", "", string.punctuation))
    return {word for word in cleaned.split() if word not in STOPWORDS}


def same(left, right):
    a, b = words(left), words(right)
    return bool(a | b) and len(a & b) / len(a | b) >= SAME


def claims(sections):
    """The numbered claims under ## Claims, without their numbers."""
    found = re.findall(r"^\s*\d+[.)]\s+(.+)$", sections.get("## Claims", ""), re.MULTILINE)
    assert all(text.strip() for text in found), "a claim has text"
    return [text.strip() for text in found]


def head_line(job, state, meta, marked):
    """JOB STATE wall cost tokens confidence verified k/n."""
    parts = [job, state]
    wall, cost = meta.get("wall_s"), meta.get("cost_usd")
    parts += [f"{int(wall) // 60}m{int(wall) % 60:02d}s"] if isinstance(wall, (int, float)) else []
    parts += [f"${cost:.2f}" if isinstance(cost, (int, float)) else "cost=null"]
    tokens = [meta.get(k) for k in ("input_tokens", "output_tokens")]
    if all(isinstance(t, int) for t in tokens):
        parts.append(f"tok={sum(tokens) // 1000}k")
    if state == "DONE":
        parts.append(f"verified {sum(m == '+' for m, _ in marked)}/{len(marked)}")
    return " ".join(parts)


def short(text):
    one = " ".join(text.split())
    return one if len(one) <= MAX_CLAIM_CHARS else one[: MAX_CLAIM_CHARS - 1] + "…"


if len(sys.argv) != 3 or sys.argv[1] == sys.argv[2]:
    fail(2, "usage: agent_compare JOB_A JOB_B   (two different jobs)")
for job in sys.argv[1:]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job):
        fail(2, f"bad job name {job!r}")
sides = []
for job in sys.argv[1:]:
    state, sections, meta = load(job)
    marked = [(mark(c, meta.get("dir")), c) for c in claims(sections)]
    sides.append((job, state, sections, meta, marked))
(job_a, state_a, sec_a, meta_a, marked_a), (job_b, state_b, sec_b, meta_b, marked_b) = sides
out = [" · ".join(head_line(job, state, meta, marked) for job, state, _, meta, marked in sides)]
done = [side for side in sides if side[1] == "DONE"]
if len(done) == 2:
    verdict_a, verdict_b = sec_a["## Verdict"].strip(), sec_b["## Verdict"].strip()
    agree = "AGREE" if same(verdict_a, verdict_b) else "DIFFER"
    out.append(f'verdict A: "{short(verdict_a)}"  verdict B: "{short(verdict_b)}"  → {agree}')
    pairs = [(i, j) for i, (_, a) in enumerate(marked_a) for j, (_, b) in enumerate(marked_b)
             if same(a, b)]
    only_a = [i for i in range(len(marked_a)) if all(p[0] != i for p in pairs)]
    only_b = [j for j in range(len(marked_b)) if all(p[1] != j for p in pairs)]
    agreed = list(dict.fromkeys(i for i, _ in pairs))
    out.append(f"agreed ({len(agreed)}) · only-A ({len(only_a)}) · only-B ({len(only_b)})")
    for label, marked, picked in (("A", marked_a, only_a), ("B", marked_b, only_b),
                                  ("=", marked_a, agreed)):
        listed = picked[:MAX_LISTED]
        out += [f"{label}#{i + 1} {marked[i][0]} {short(marked[i][1])}" for i in listed]
elif done:
    job, _state, sections, _meta, marked = done[0]
    out.append(f'single source {job}: verdict "{short(sections["## Verdict"].strip())}"')
    out += [f"#{i + 1} {m} {short(c)}" for i, (m, c) in enumerate(marked[:MAX_LISTED])]
packet = "\n".join(out).encode()
room = MAX_PACKET_BYTES - 1  # print adds one newline; stdout as a whole stays under the cap
if len(packet) > room:  # hard cap; cut on a character boundary and say so
    packet = packet[: room - 4].decode("utf-8", "ignore").encode() + "\n…".encode()
assert len(packet) <= room, "the packet never outgrows its cap"
print(packet.decode())
sys.exit({2: 0, 1: 3, 0: 1}[len(done)])
'''


AGENT_STATS = r'''"""agent_stats — which delegate to ask: per agent, from <run_dir>/*/status.json
and meta.json ($GRIM_RUN_DIR or ~/.grimoire/run): asks, DONE rate, median wall time,
median cost_usd ("unknown" unless one was above 0), counts per failure state (and
"no-status", "bad-status": never a crash).
Usage: agent_stats [DAYS]   (default 30; by status.json mtime, else the dir's)
Ref: https://docs.python.org/3/library/statistics.html#statistics.median"""
import argparse
import itertools
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

DAY_S = 86400
MAX_ENTRIES = 10_000  # run dir entries read at most; a bigger run dir is scored this far
AGENTS = ("claude", "codex", "pi")  # a job named ID-AGENT with no recorded agent
MARKERS = ("brief.md", "meta.json", "status.json", "result.md")  # a delegate's job dir
RUN_DIR = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")


def load(path):
    """(record, None), or (None, why): a missing or broken file is data, not a crash."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return None, "no-status" if isinstance(error, FileNotFoundError) else "bad-status"
    return (record, None) if isinstance(record, dict) else (None, "bad-status")


def ask(job_dir):
    """(agent, state, wall_s, cost_usd, mtime) of one job dir."""
    status_path = job_dir / "status.json"
    status, why = load(status_path)
    meta = load(job_dir / "meta.json")[0] or {}
    guess = job_dir.name.rsplit("-", 1)[-1]
    agent = meta.get("agent") or (status or {}).get("agent") or (
        guess if guess in AGENTS else "unknown")
    state = str((status or {}).get("state") or why or "no-state")
    mtime = (status_path if status_path.exists() else job_dir).stat().st_mtime
    wall, cost = (meta.get(key) if type(meta.get(key)) in (int, float) else None  # bool is not
                  for key in ("wall_s", "cost_usd"))
    return str(agent), state, wall, cost if cost and cost > 0 else None, mtime


parser = argparse.ArgumentParser(prog="agent_stats")
parser.add_argument("days", nargs="?", type=float, default=30.0, metavar="DAYS",
                    help="window in days, by status.json mtime (default: 30)")
opts = parser.parse_args()
if opts.days <= 0:
    parser.error("DAYS is a number above 0")
since, asks = time.time() - opts.days * DAY_S, defaultdict(list)
entries = itertools.islice(RUN_DIR.iterdir(), MAX_ENTRIES) if RUN_DIR.is_dir() else ()
for job_dir in entries:
    if job_dir.is_dir() and any((job_dir / marker).exists() for marker in MARKERS):
        agent, *row = ask(job_dir)
        if row[-1] >= since:
            asks[agent].append(row)
total = sum(map(len, asks.values()))
print(f"agent_stats: {total} asks in the last {opts.days:g} days in {RUN_DIR}")
print(f"{'AGENT':<8} {'ASKS':>4} {'DONE':>4} {'DONE%':>5} {'WALL':>7}  {'COST':<14} FAILURES")
for agent in sorted(asks):
    rows = asks[agent]
    done = sum(state == "DONE" for state, *_ in rows)
    walls = [wall for _, wall, _, _ in rows if wall is not None]
    costs = [cost for _, _, cost, _ in rows if cost is not None]
    wall = divmod(int(statistics.median(walls)), 60) if walls else None
    wall_text = f"{wall[0]}m{wall[1]:02d}s" if wall else "unknown"
    cost_text = f"${statistics.median(costs):.4f} ({len(costs)})" if costs else "unknown"
    failed = Counter(state for state, *_ in rows if state != "DONE")
    failures = " ".join(f"{state}={n}" for state, n in sorted(failed.items())) or "-"
    print(f"{agent:<8} {len(rows):>4} {done:>4} {100 * done // len(rows):>4}% {wall_text:>7}  "
          f"{cost_text:<14} {failures}")
'''

# (name, description, body) rows, as in bodies_tmux.py.
COMPARE_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_compare",
        "compare two delegates' answers with no model: agent_compare JOB_A JOB_B prints one "
        "packet (<= 4 KiB): verdicts AGREE/DIFFER, agreed and only-A/only-B claims, evidence",
        AGENT_COMPARE,
    ),
    (
        "agent_stats",
        "which delegate to ask? score claude, codex and pi by past asks: agent_stats [DAYS] — "
        "asks, DONE rate, median wall time and cost, failures, from <run_dir>/*/status.json",
        AGENT_STATS,
    ),
)
