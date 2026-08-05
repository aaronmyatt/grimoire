#!/usr/bin/env python3
"""Internal helpers shared by the six verb modules — not public surface
(verbs/CLAUDE.md). Verbs never import each other; all may import this.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess

from grim import db

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


# language -> syntax-checker argv (body fed on stdin). Keep the set small:
# a checker must be cheap, offline, and read stdin — no temp files. A missing
# binary degrades to no lint, never a write blocker (`grim doctor` reports
# missing interpreters instead).
_SUBPROCESS_LINT: dict[str, list[str]] = {
    "ruby": ["ruby", "-c"],
    "php": ["php", "-l"],
    "perl": ["perl", "-c"],
    "go": ["gofmt", "-e"],
}


def _subprocess_lint(argv: list[str], body: str, language: str) -> str | None:
    """Run a syntax checker that reads `body` on stdin: None when it passes, a
    diagnostic string when it fails. A missing checker binary (OSError) means
    no lint — lint is best-effort, and `grim doctor` owns reporting missing
    interpreters."""
    try:
        result = subprocess.run(argv, input=body, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return f"{language} syntax error: {result.stderr.strip() or result.stdout.strip()}"
    return None


def connect() -> sqlite3.Connection:
    """`db.connect()` with row_factory set, so verb SQL can use `row["col"]`."""
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    return conn


def validate_slug(name: str) -> None:
    """Raise ValueError if `name` doesn't match the script naming API."""
    if not SLUG_RE.match(name):
        raise ValueError(f"invalid script name {name!r} — must match {SLUG_RE.pattern}")
    assert SLUG_RE.match(name), "validate_slug must reject anything it didn't just accept"


def lint(language: str, body: str) -> str | None:
    """Diagnostic string if `body` fails syntax lint, else None. Unknown
    languages, and languages without a cheap offline checker, pass silently;
    opt-in extended languages get best-effort lints where one exists."""
    if language == "python":
        try:
            compile(body, "<grim write>", "exec")
        except SyntaxError as exc:
            return f"python syntax error: {exc}"
        return None
    if language == "bash":
        result = subprocess.run(
            ["bash", "-n"], input=body, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return f"bash syntax error: {result.stderr.strip()}"
        return None
    if language in _SUBPROCESS_LINT:
        return _subprocess_lint(_SUBPROCESS_LINT[language], body, language)
    return None


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def parse_name_version(spec: str) -> tuple[str, int | None]:
    """Parses bare `NAME` to (NAME, None) and `NAME@3` to (NAME, 3)."""
    if "@" not in spec:
        return spec, None
    name, _, version_str = spec.rpartition("@")
    return name, int(version_str)


def resolve_script_version(conn: sqlite3.Connection, name: str, version: int | None) -> sqlite3.Row:
    """Fetch script_version joined with script; latest when version is
    None. Raises LookupError if missing — external input, not an assert.
    """
    query = (
        "SELECT sv.id, sv.script_id, sv.version, sv.body, sv.body_hash, "
        "s.name, s.language, s.description "
        "FROM script_version sv JOIN script s ON s.id = sv.script_id WHERE s.name = ?"
    )
    params: list[object] = [name]
    if version is None:
        query += " ORDER BY sv.version DESC LIMIT 1"
    else:
        query += " AND sv.version = ?"
        params.append(version)
    row = conn.execute(query, params).fetchone()
    if row is None:
        spec = name if version is None else f"{name}@{version}"
        raise LookupError(f"script {spec!r} not found")
    assert isinstance(row, sqlite3.Row), "connect() must set row_factory = sqlite3.Row"
    assert row["name"] == name, "resolved row must match the requested script name"
    return row


def ensure_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Lazily create the session row FK-dependent inserts need (no verb
    creates sessions explicitly). Caller commits; this only stages it."""
    kind = "human" if session_id == "human-adhoc" else "agent"
    conn.execute("INSERT OR IGNORE INTO session (id, kind) VALUES (?, ?)", (session_id, kind))


def default_scope() -> str:
    """Repo-scoped by default when cwd is a git repo, else global (D10)."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return "global"
    toplevel = result.stdout.strip()
    assert toplevel, "git rev-parse succeeded but returned an empty toplevel path"
    fingerprint = hashlib.sha256(toplevel.encode()).hexdigest()[:12]
    return f"repo:{fingerprint}"


def fts_match_query(text: str) -> str:
    """Tokenize `text` into an FTS5 MATCH expression (OR'd, quoted tokens)
    — shared by write's similarity nudge and the `find` verb."""
    tokens = re.findall(r"[A-Za-z0-9_]+", text)
    return " OR ".join(f'"{t}"' for t in tokens)


def similar_scripts(
    conn: sqlite3.Connection, query: str, limit: int = 3
) -> list[tuple[str, float]]:
    """FTS5 MATCH ranked by bm25 — the write-time nudge (build plan §4).
    bm25() is more-negative-is-better; sign-flipped so higher = closer."""
    match_query = fts_match_query(query)
    if not match_query:
        return []
    rows = conn.execute(
        "SELECT s.name, bm25(script_fts) AS rank FROM script_fts "
        "JOIN script s ON s.id = script_fts.rowid "
        "WHERE script_fts MATCH ? ORDER BY rank LIMIT ?",
        (match_query, limit),
    ).fetchall()
    assert len(rows) <= limit, "similar_scripts must never return more than the requested limit"
    return [(row["name"], -row["rank"]) for row in rows]


def session_id_from_env() -> str:
    """GRIM_SESSION (adapter-set) or 'human-adhoc' (build plan §4)."""
    return os.environ.get("GRIM_SESSION", "human-adhoc")
