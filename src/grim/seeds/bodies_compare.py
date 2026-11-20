"""Seed body that arbitrates two delegates' answers mechanically, with no model
(docs/delegation-plan.md, Phase 3b; packet shape in §5). Data, not code:
bodies.py's SEEDS roster wires COMPARE_SEEDS in."""

from __future__ import annotations

AGENT_COMPARE = r'''"""agent_compare — one packet comparing two delegates'
answers, with no model in the loop. State comes from <run_dir>/JOB/status.json
only (DONE needs agent_done's sha to still match result.md). Each DONE
result.md is read by heading. Claims match when their words
overlap (Jaccard >= 0.5, after lowercasing, dropping punctuation and a fixed
stopword list); so do the verdicts: AGREE, else DIFFER.
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

MAX_CLAIM_CHARS, MAX_LISTED, SAME = 160, 8, 0.5
MAX_RESULT_BYTES = 16384  # agent_done's cap on result.md
HEADINGS = ("## Verdict", "## Answer", "## Claims", "## Confidence", "## Open")
STOPWORDS = frozenset(
    "a an and are as at be but by for from has have in is it its of on or that the "
    "their there this to was were will with which not no can should does do so than".split()
)
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
    """JOB STATE wall cost tokens."""
    parts = [job, state]
    wall, cost = meta.get("wall_s"), meta.get("cost_usd")
    parts += [f"{int(wall) // 60}m{int(wall) % 60:02d}s"] if isinstance(wall, (int, float)) else []
    parts += [f"${cost:.2f}" if isinstance(cost, (int, float)) else "cost=null"]
    tokens = [meta.get(k) for k in ("input_tokens", "output_tokens")]
    if all(isinstance(t, int) for t in tokens):
        parts.append(f"tok={sum(tokens) // 1000}k")
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
    marked = [("", c) for c in claims(sections)]  # (evidence mark, claim)
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
        out += [f"{label}#{i + 1} {short(marked[i][1])}" for i in listed]
elif done:
    job, _state, sections, _meta, marked = done[0]
    out.append(f'single source {job}: verdict "{short(sections["## Verdict"].strip())}"')
    out += [f"#{i + 1} {short(c)}" for i, (_m, c) in enumerate(marked[:MAX_LISTED])]
print("\n".join(out))
sys.exit({2: 0, 1: 3, 0: 1}[len(done)])
'''

# (name, description, body) rows, as in bodies_tmux.py.
COMPARE_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_compare",
        "compare two delegates' answers with no model: agent_compare JOB_A JOB_B prints one "
        "packet (<= 4 KiB): verdicts AGREE/DIFFER, agreed and only-A/only-B claims, evidence",
        AGENT_COMPARE,
    ),
)
