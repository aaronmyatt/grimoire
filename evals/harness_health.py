#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# ///
"""harness_health — the before/after ruler for grim-agent harness changes
(docs/python-pivot-plan.md §1). Stdlib only, read-only.

Reads the library ($GRIM_DB, else ~/.grimoire/grimoire.db) and the span log
($GRIM_INSTRUMENT_FILE, else ~/.grimoire/instrumentation.jsonl), prints the
failure/usage metrics, and with --check asserts the logging invariants on
sessions newer than --since (exit 1 on any violation).

Usage:
  uv run evals/harness_health.py [--since YYYY-MM-DD] [--check]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Seeds / scripts that run arbitrary commands: the "escape hatch" share.
ESCAPE_HATCHES = ("shell", "run_sh", "run_cmd", "run_py")
TOP_ERRORS = 10
EXIT_TIMEOUT = 124
EXIT_NOT_FOUND = 127
_P90 = 0.9


def _home_file(env: str, name: str) -> Path:
    return Path(os.environ.get(env) or Path.home() / ".grimoire" / name).expanduser()


def load_sessions(path: Path, since_ms: int) -> dict[str, dict[str, Any]]:
    """Span-log records grouped per session: {"open", "close", "spans"}.
    Unparseable lines are skipped — the log is append-only, never trusted."""
    sessions: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"open": None, "close": None, "spans": []}
    )
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        session = record.get("session")
        if not session or record.get("ts", 0) < since_ms:
            continue
        if record.get("kind") == "session":
            sessions[session][record.get("phase", "?")] = record
        else:
            sessions[session]["spans"].append(record)
    return dict(sessions)


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))] if ordered else 0


def span_metrics(sessions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    spans = [s for v in sessions.values() for s in v["spans"]]
    calls = [s for s in spans if s.get("phase") == "llm.completion"]
    format_errors = [s for s in spans if s.get("error") == "FormatError"]
    tokens = [
        sum(s.get("total_tokens", 0) for s in v["spans"] if s.get("phase") == "llm.completion")
        for v in sessions.values()
    ]
    outcomes = collections.Counter(
        (v["close"] or {}).get("exit_status", "unrecorded") if v["close"] else "no close record"
        for v in sessions.values()
    )
    messages = collections.Counter(s.get("error_msg", "<no error_msg>") for s in format_errors)
    return {
        "sessions": len(sessions),
        "outcomes": outcomes.most_common(),
        "llm_calls": len(calls),
        "format_errors": len(format_errors),
        "top_format_errors": messages.most_common(TOP_ERRORS),
        "tokens_median": _percentile(tokens, 0.5),
        "tokens_p90": _percentile(tokens, _P90),
        "prompt_p90": _percentile([s.get("prompt_tokens", 0) for s in calls], _P90),
    }


def db_metrics(conn: sqlite3.Connection, since: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT s.name, COALESCE(v.language, s.language), e.exit_code FROM execution e "
        "JOIN script_version v ON v.id = e.script_version_id JOIN script s ON s.id = v.script_id "
        "WHERE e.started_at >= ?",
        (since,),
    ).fetchall()
    fails = [r for r in rows if r[2] != 0]
    by_lang = collections.Counter(r[1] for r in rows)
    fail_by_lang = collections.Counter(r[1] for r in fails)
    single_use = conn.execute(
        "SELECT AVG(n <= 1) FROM (SELECT COUNT(DISTINCT e.session_id) n FROM script s "
        "LEFT JOIN script_version v ON v.script_id = s.id "
        "LEFT JOIN execution e ON e.script_version_id = v.id "
        "WHERE s.seeded = 0 AND s.created_at >= ? GROUP BY s.id)",
        (since,),
    ).fetchone()[0]
    return {
        "runs": len(rows),
        "fail_pct": 100 * len(fails) / len(rows) if rows else 0.0,
        "fail_pct_by_lang": {k: 100 * fail_by_lang[k] / n for k, n in by_lang.items()},
        "escape_pct": 100 * sum(r[0] in ESCAPE_HATCHES for r in rows) / len(rows) if rows else 0,
        "timeouts": sum(r[2] == EXIT_TIMEOUT for r in rows),
        "not_found": sum(r[2] == EXIT_NOT_FOUND for r in rows),
        "single_use_pct": 100 * (single_use or 0.0),
    }


def check_invariants(
    conn: sqlite3.Connection, sessions: dict[str, dict[str, Any]], since: str
) -> list[str]:
    """Logging invariants (plan L1–L6); each violated rule is one line."""
    spans = [s for v in sessions.values() for s in v["spans"]]
    rules = {
        "agent session rows with NULL model": conn.execute(
            "SELECT COUNT(*) FROM session WHERE kind = 'agent' AND model IS NULL "
            "AND started_at >= ?",
            (since,),
        ).fetchone()[0],
        "FormatError spans without error_msg": sum(
            s.get("error") == "FormatError" and "error_msg" not in s for s in spans
        ),
        "tool spans without returncode": sum(
            s.get("phase", "").startswith("tool.")
            and s.get("phase") != "tool.submit"
            and "returncode" not in s
            for s in spans
        ),
        "sessions with no close exit_status": sum(
            "exit_status" not in (v["close"] or {}) for v in sessions.values() if v["open"]
        ),
    }
    return [f"{count} {rule}" for rule, count in rules.items() if count]


def render(spans: dict[str, Any], lib: dict[str, Any]) -> str:
    fe_pct = 100 * spans["format_errors"] / spans["llm_calls"] if spans["llm_calls"] else 0.0
    lines = [
        f"sessions traced        {spans['sessions']}",
        f"outcomes               {spans['outcomes']}",
        f"runs                   {lib['runs']}  fail {lib['fail_pct']:.1f}%",
        f"fail % by language     { {k: round(v, 1) for k, v in lib['fail_pct_by_lang'].items()} }",
        f"escape-hatch share     {lib['escape_pct']:.1f}%  ({', '.join(ESCAPE_HATCHES)})",
        f"timeouts / not found   {lib['timeouts']} / {lib['not_found']}",
        f"single-use scripts     {lib['single_use_pct']:.0f}%",
        f"format errors          {spans['format_errors']} / {spans['llm_calls']} ({fe_pct:.1f}%)",
        f"tokens/session         median {spans['tokens_median']:,}  p90 {spans['tokens_p90']:,}",
        f"prompt tokens p90      {spans['prompt_p90']:,}",
        "top format errors:",
        *(f"  {n:4}  {msg}" for msg, n in spans["top_format_errors"]),
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="1970-01-01", help="YYYY-MM-DD (UTC)")
    parser.add_argument("--check", action="store_true", help="assert logging invariants")
    args = parser.parse_args(argv)
    try:
        since_dt = dt.datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    except ValueError:
        parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")
    sessions = load_sessions(
        _home_file("GRIM_INSTRUMENT_FILE", "instrumentation.jsonl"),
        int(since_dt.timestamp() * 1000),
    )
    conn = sqlite3.connect(f"file:{_home_file('GRIM_DB', 'grimoire.db')}?mode=ro", uri=True)
    try:
        print(render(span_metrics(sessions), db_metrics(conn, args.since)))
        violations = check_invariants(conn, sessions, args.since) if args.check else []
    finally:
        conn.close()
    for violation in violations:
        print(f"CHECK FAILED: {violation}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
