"""`grim find` — FTS5 ranked search: name > description > body (build plan
§4). Phase 6b re-rank: exact slug first; usage breaks near-ties only. No embeddings.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
from typing import Any

from grim.verbs import _shared

DEFAULT_FIND_LIMIT = 5
# One-off scripts (probes, verifications, single-use fixes) take this name
# prefix: still runnable and recorded, but never crowding find's results —
# 35% of the agent's library was such throwaways, reused in another session
# only 16% of the time. A query that itself starts with "scratch" opts back
# in. GLOB, not LIKE: `_` is literal there.
# Ref: https://www.sqlite.org/lang_expr.html#glob
SCRATCH_PREFIX = "scratch_"
CANDIDATE_CAP = 50  # FTS hits pulled per query, then re-ranked in Python
# A hit is plausible when its relevance is at least this share of the best in its group. 0.8
# lets usage break near-ties only; a hit matching one weak word of three stays below it.
PLAUSIBLE_RATIO = 0.8
# The FTS5 unicode61 tokenizer splits on anything not a letter or digit, "_" included.
# Ref: https://www.sqlite.org/fts5.html#unicode61_tokenizer
TOKEN_RE = re.compile(r"[a-z0-9]+")
# Words that say nothing about which script is wanted: left out of the FTS query (else "a" fills
# the candidate pool) and of coverage (else "for" makes run_py plausible for any query).
STOPWORDS = frozenset(
    "a an and are at be by did do does for from how i in is it its me my of on or the this "
    "to what which with".split()
)
_COLUMNS = "s.id, s.name, s.description, s.language"
_SCRATCH_FILTER = "s.archived = 0 AND (? OR s.name NOT GLOB ?)"


def _usage(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    """script_health's sums for these scripts only: that view aggregates every execution (~26 ms
    on a 14k-run library; execution.script_version_id has no index) and find needs <= 51."""
    assert len(ids) <= CANDIDATE_CAP + 1, "usage is looked up for candidates only"
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT v.script_id AS id, COUNT(e.id) AS runs, AVG(e.exit_code = 0) AS success_rate, "
        "MAX(e.started_at) AS last_used FROM execution e "
        f"JOIN script_version v ON v.id = e.script_version_id WHERE v.script_id IN ({marks}) "
        "GROUP BY v.script_id",
        ids,
    ).fetchall()
    assert all(row["runs"] > 0 for row in rows), "a script with no runs has no usage row"
    return {row["id"]: row for row in rows}


def _rerank(rows: list[sqlite3.Row], query: str, usage: dict[int, sqlite3.Row]) -> list[int]:
    """Candidate order: field tier, provenance tier, then plausible hits (as many query words as
    the group's best, bm25 within PLAUSIBLE_RATIO of it) by usage, then bm25."""
    words = set(TOKEN_RE.findall(query.lower())) - STOPWORDS
    found = [set(TOKEN_RE.findall(f"{r['name']} {r['description']}".lower())) for r in rows]
    cover = [len(words & tokens) for tokens in found]
    groups = [(0 if cover[i] else 1, row["tier"]) for i, row in enumerate(rows)]
    best: dict[tuple[int, int], tuple[int, float]] = {}
    for i, group in enumerate(groups):  # bm25 is negative: more negative is more relevant
        top_cover, top_rank = best.get(group, (0, 0.0))
        best[group] = (max(top_cover, cover[i]), min(top_rank, rows[i]["rank"]))

    def key(i: int) -> tuple[int, int, int, float, float]:
        row, (top_cover, top_rank) = rows[i], best[groups[i]]
        if cover[i] < top_cover or row["rank"] > PLAUSIBLE_RATIO * top_rank:
            return (*groups[i], 1, 0.0, row["rank"])
        hit = usage.get(row["id"])
        prior = math.log1p(hit["runs"]) * hit["success_rate"] if hit else 0.0
        return (*groups[i], 0, -prior, row["rank"])

    order = sorted(range(len(rows)), key=key)
    assert sorted(order) == list(range(len(rows))), "re-ranking only reorders candidates"
    return order


def find_scripts(
    conn: sqlite3.Connection, query: str, limit: int = DEFAULT_FIND_LIMIT, scope: str | None = None
) -> list[dict[str, Any]]:
    """Ranked hits for `query` (see the module docstring). Tiers use `scope` — the current repo;
    None (or 'global' outside a repo) leaves only the global/other tiers. `scratch_*` scripts are
    left out unless the query starts with "scratch" (SCRATCH_PREFIX)."""
    assert scope is None or _shared.SCOPE_RE.match(scope), "scope must be resolved before find"
    assert limit >= 1, "find needs a positive limit"
    content = [word for word in query.split() if word.lower() not in STOPWORDS]
    match_query = _shared.fts_match_query(" ".join(content) or query)  # all stopwords: keep them
    if not match_query:
        return []
    include_scratch = query.strip().lower().startswith(SCRATCH_PREFIX.rstrip("_"))
    scratch = (include_scratch, SCRATCH_PREFIX + "*")
    rows = conn.execute(
        f"SELECT {_COLUMNS}, bm25(script_fts, 10.0, 5.0, 1.0) AS rank, CASE WHEN s.scope = ? "
        "THEN 0 WHEN s.scope = 'global' THEN 1 ELSE 2 END AS tier FROM script_fts "
        f"JOIN script s ON s.id = script_fts.rowid WHERE script_fts MATCH ? AND {_SCRATCH_FILTER} "
        "ORDER BY tier, rank LIMIT ?",
        (scope or "", match_query, *scratch, CANDIDATE_CAP),
    ).fetchall()
    slug = "_".join(query.lower().split())
    exact = conn.execute(
        f"SELECT {_COLUMNS} FROM script s WHERE s.name = ? AND {_SCRATCH_FILTER}", (slug, *scratch)
    ).fetchone()
    usage = _usage(conn, [row["id"] for row in rows] + ([exact["id"]] if exact else []))
    ordered = [rows[i] for i in _rerank(rows, query, usage)]
    if exact is not None:
        ordered = [exact, *(row for row in ordered if row["id"] != exact["id"])]
    none: dict[str, Any] = {"runs": 0, "success_rate": 0.0, "last_used": None}
    hits = [{**dict(row), **dict(usage.get(row["id"], none))} for row in ordered[:limit]]
    assert len(hits) <= limit, "find_scripts must never exceed the requested limit"
    assert include_scratch or not any(h["name"].startswith(SCRATCH_PREFIX) for h in hits), (
        "scratch scripts surface only when the query asks for them"
    )
    return hits


def cmd_find(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        rows = find_scripts(
            conn, args.query, args.limit or DEFAULT_FIND_LIMIT, scope=_shared.default_scope()
        )
        for row in rows:
            # last_used is NULL until a script's first run; show "-" so the column
            # stays aligned rather than printing the literal "None".
            print(
                f"{row['name']}\t{row['language']}\t{row['description']}"
                f"\truns={row['runs']}\tsuccess={row['success_rate']:.2f}"
                f"\tlast={row['last_used'] or '-'}"
            )
        return 0
    finally:
        conn.close()
