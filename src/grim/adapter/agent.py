"""GrimAgent — subclasses mini-swe-agent's InteractiveAgent to prepopulate
the system prompt with strong FTS matches for the raw task text, run once
before the agent takes its first turn.

Rationale: the post-Phase-3 protocol made `grim find` conditional, not
mandatory (build plan §6, §8 Risks: "Optional find misses/duplicates
existing scripts"). This closes part of that gap mechanically — a
high-confidence match is surfaced up front, the same way the seed list
already is, instead of depending on the agent choosing to search.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from minisweagent.agents.interactive import InteractiveAgent

from grim import db
from grim.adapter.bang import install_bang_expansion
from grim.adapter.completer import install_grim_completer
from grim.adapter.environment import GrimEnvironment

# Operator-authored system-prompt extension, the agent-harness analogue of a
# global ~/.claude or ~/.pi instruction file. Lives under grim's home dir (the
# same ~/.grimoire that holds the DB and config.toml). Read fresh each run so
# edits take effect without a reinstall.
SYSTEM_PROMPT_PATH = Path.home() / ".grimoire" / "system.md"

# bm25() in FTS5 is more-negative-is-better (https://sqlite.org/fts5.html
# #the_bm25_function); sign-flipped here so higher = closer, matching
# verbs/_shared.py's similar_scripts convention. This threshold is
# deliberately strict — the whole point is a short, high-confidence list,
# not a lower-recall echo of `grim find`. Tune alongside real usage data.
STRONG_MATCH_THRESHOLD = 6.0
STRONG_MATCH_LIMIT = 3


def _match_query(text: str) -> str:
    """Tokenize into an FTS5 MATCH expression (OR'd, quoted tokens) — a
    small, adapter-owned duplicate of verbs/_shared.py's fts_match_query.
    Slices don't import each other's internals (root CLAUDE.md §2); this
    is a few lines, not worth a shared-kernel promotion."""
    tokens = re.findall(r"[A-Za-z0-9_]+", text)
    return " OR ".join(f'"{t}"' for t in tokens)


def strong_matches(task: str) -> list[dict[str, str]]:
    """Strict FTS5 hits for `task` against the script library — empty if
    nothing clears STRONG_MATCH_THRESHOLD, never partial/best-effort
    matches. Uses find.py's column weighting (name > description > body)
    since this is effectively a pre-run, high-confidence `grim find`."""
    match_query = _match_query(task)
    if not match_query:
        return []
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.name, s.language, s.description, "
            "-bm25(script_fts, 10.0, 5.0, 1.0) AS score "
            "FROM script_fts JOIN script s ON s.id = script_fts.rowid "
            "WHERE script_fts MATCH ? AND s.archived = 0 "
            "AND -bm25(script_fts, 10.0, 5.0, 1.0) >= ? "
            "ORDER BY score DESC LIMIT ?",
            (match_query, STRONG_MATCH_THRESHOLD, STRONG_MATCH_LIMIT),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) <= STRONG_MATCH_LIMIT, "strong_matches must never exceed its own limit"
    return [
        {"name": row["name"], "language": row["language"], "description": row["description"]}
        for row in rows
    ]


def user_prompt_extension(path: Path | None = None) -> str:
    """Operator instructions appended to the system prompt, read from
    ~/.grimoire/system.md. Absent or unreadable -> "" (external input:
    degrade, never crash). Stripped, so an empty/whitespace-only file renders
    nothing under system_template's truthiness guard."""
    prompt_path = path if path is not None else SYSTEM_PROMPT_PATH
    assert prompt_path is not None, "system-prompt path resolves to a value"
    if not prompt_path.is_file():
        return ""
    try:
        text = prompt_path.read_text()
    except OSError:
        return ""
    result = text.strip()
    assert isinstance(result, str), "extension is always a string"
    return result


# --- library recall (--continue) --------------------------------------------
# On --continue the agent is warm-started with its own recently-valuable
# scripts (name + description, never bodies), so it reuses accumulated tools
# instead of rewriting them. Sibling of strong_matches(): a direct, terse
# library read stashed into extra_template_vars for instance_template to render
# — placed by the task (not the system prompt) so recency bias weighs it.

RECALL_LIMIT_DEFAULT = 10  # scripts to recall; GRIM_RECALL_LIMIT overrides
RECALL_LIMIT_MAX = 50  # hard ceiling — a bounded collection, never unbounded (§1)
RECALL_MIN_SUCCESS = 0.5  # skip mostly-failing scripts; a recalled tool should work
RECALL_POOL_FACTOR = 3  # recency-bounded candidate pool = limit * this, then value-ranked


def recall_enabled() -> bool:
    """True when --continue set GRIM_RECALL (or it was exported directly). Off
    by default, so a normal run's prompt is byte-for-byte unchanged."""
    return bool(os.environ.get("GRIM_RECALL"))


def recall_limit() -> int:
    """How many scripts to recall: GRIM_RECALL_LIMIT clamped to [1, MAX], else
    the default. External input -> validate, never trust (constitution §3)."""
    raw = os.environ.get("GRIM_RECALL_LIMIT")
    if raw is None:
        return RECALL_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return RECALL_LIMIT_DEFAULT
    return max(1, min(value, RECALL_LIMIT_MAX))


def rank_recall(candidates: list[dict[str, Any]], k: int) -> list[dict[str, str]]:
    """Pure: pick the k most-valuable scripts (most runs, then most iterated),
    then order them by last_used ASCENDING so the most-recently-used lands LAST
    — nearest the task, where recency bias weighs heaviest. Terse (name +
    description only); no DB, no clock, deterministic for a given input."""
    assert k >= 1, "recall limit must be positive"
    by_value = sorted(candidates, key=lambda c: (c["runs"], c["iterations"]), reverse=True)
    chosen = by_value[:k]
    chosen.sort(key=lambda c: c["last_used"])  # ISO-ish text sorts chronologically
    result = [{"name": str(c["name"]), "description": str(c["description"])} for c in chosen]
    assert len(result) <= k, "recall never exceeds its limit"
    return result


# Ranked so the SQL LIMIT bounds a recency pool; rank_recall re-ranks by value.
_RECALL_SQL = (
    "SELECT s.name, s.description, h.runs, "
    "(SELECT MAX(version) FROM script_version WHERE script_id = s.id) AS iterations, "
    "h.last_used "
    "FROM script s JOIN script_health h ON h.id = s.id "
    "WHERE s.archived = 0 AND s.seeded = 0 AND h.runs > 0 AND h.success_rate >= ? "
    "ORDER BY h.last_used DESC LIMIT ?"
)


def recent_library(limit: int) -> list[dict[str, str]]:
    """The agent's own recently-valuable scripts, ranked for recall: a
    recency-bounded pool from the library (non-seeded, non-archived, run at
    least once, not mostly-failing), value-ranked via rank_recall."""
    assert limit >= 1, "recall limit must be positive"
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:
        pool = conn.execute(
            _RECALL_SQL, (RECALL_MIN_SUCCESS, limit * RECALL_POOL_FACTOR)
        ).fetchall()
    finally:
        conn.close()
    return rank_recall([dict(row) for row in pool], limit)


class GrimAgent(InteractiveAgent):
    """Same contract as InteractiveAgent; only run() is extended so
    system_template can reference {{ grim_strong_matches }} / the operator's
    {{ grim_user_prompt }}, and (on --continue) instance_template can render
    {{ grim_recent_library }}."""

    def run(self, task: str = "", **kwargs: object) -> dict[str, object]:
        self.extra_template_vars["grim_strong_matches"] = strong_matches(task)
        self.extra_template_vars["grim_user_prompt"] = user_prompt_extension()
        if recall_enabled():
            self.extra_template_vars["grim_recent_library"] = recent_library(recall_limit())
        # Enable @/: completion and !slug execute-and-substitute on mini's
        # prompt sessions (no-op without a TTY).
        install_grim_completer()
        env = self.env
        assert isinstance(env, GrimEnvironment), "GrimAgent always runs with GrimEnvironment"
        install_bang_expansion(env.session_id)
        return super().run(task, **kwargs)
