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

# script.scope is either 'global' or a 12-hex repo id (the repo's root-commit
# hash — see repo_identity). The retired 'repo:<pathhash>' shape simply never
# matches a current scope, so legacy rows rank last in `find`'s scope tiers.
SCOPE_RE = re.compile(r"^global$|^[0-9a-f]{12}$")
_ROOT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# Mirrors curate/tags.py's TAG_RE shape (lowercase slug, ≤ 32 chars total)
# under the fixed 'repo-' prefix — a deliberate cross-slice duplicate (slices
# don't import each other, root CLAUDE.md §2), flagged in ABSTRACTIONS.md.
_REPO_TAG_RE = re.compile(r"^repo-[a-z0-9][a-z0-9-]{0,26}$")
_REPO_TAG_NAME_MAX = 27  # 32-char tag budget minus the 'repo-' prefix
_SCOPE_HEX_CHARS = 12  # truncation length of the root-commit hash in scope


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


def _git_out(argv: list[str]) -> str | None:
    """Stripped stdout of a git query, or None on nonzero exit / missing git —
    repo detection is best-effort and must never block a write."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def repo_identity() -> tuple[str, str] | None:
    """(scope, tag) identifying the enclosing git repo, or None outside one.

    scope is the repo's oldest root-commit hash truncated to 12 hex chars —
    stable across clones, worktrees, and directory renames, unlike a
    toplevel-path hash (D10 revised). A repo with no commits yet degrades to
    the path hash, keeping the same shape. tag is 'repo-<toplevel basename>'
    normalized to curate's tag shape: the human-readable name is provenance
    metadata only, because worktree basenames differ per checkout.
    `--max-parents=0` lists root commits, newest first, so the last line is
    the oldest root even after unrelated-history merges.
    Ref: https://git-scm.com/docs/git-rev-list#Documentation/git-rev-list.txt---max-parentsltnumbergt
    """
    toplevel = _git_out(["git", "rev-parse", "--show-toplevel"])
    if not toplevel:
        return None
    roots = (_git_out(["git", "rev-list", "--max-parents=0", "HEAD"]) or "").split()
    oldest = roots[-1] if roots else ""
    if _ROOT_COMMIT_RE.match(oldest):
        scope = oldest[:_SCOPE_HEX_CHARS]
    else:  # unborn HEAD (fresh `git init`): same 12-hex shape from the path
        scope = hashlib.sha256(toplevel.encode()).hexdigest()[:_SCOPE_HEX_CHARS]
    name = re.sub(r"[^a-z0-9-]", "-", os.path.basename(toplevel).lower())
    name = name.strip("-")[:_REPO_TAG_NAME_MAX]
    tag = f"repo-{name}" if name else f"repo-{scope}"
    assert SCOPE_RE.match(scope), "repo scope must be 12 hex chars"
    assert _REPO_TAG_RE.match(tag), "repo tag must fit curate's tag shape"
    return scope, tag


def default_scope() -> str:
    """The current repo's scope when cwd is inside a git repo, else 'global'
    (D10; identity semantics live in repo_identity)."""
    identity = repo_identity()
    scope = "global" if identity is None else identity[0]
    assert SCOPE_RE.match(scope), "default_scope must produce a valid scope"
    assert identity is None or scope != "global", "a detected repo never maps to 'global'"
    return scope


def resolve_scope(raw: str | None) -> str:
    """Normalize a caller-supplied --scope: None and the tool-schema literal
    'repo' resolve to default_scope() (so 'repo' outside a git repo degrades
    to 'global', matching the old default's behavior); 'global' and an
    explicit 12-hex repo id pass verbatim. Anything else — including the
    retired 'repo:<pathhash>' shape — is rejected: external input gets
    validation, never an assert."""
    if raw is None or raw == "repo":
        return default_scope()
    if not SCOPE_RE.match(raw):
        raise ValueError(f"invalid scope {raw!r} — expected 'global', 'repo', or a 12-hex repo id")
    assert raw == "global" or len(raw) == _SCOPE_HEX_CHARS, (
        "validated scope is 'global' or a 12-hex id"
    )
    return raw


def stamp_repo_tag(conn: sqlite3.Connection, script_id: int, scope: str) -> str | None:
    """Attach the current repo's 'repo-<name>' provenance tag to a freshly
    written script. No-op when cwd has no repo identity or its scope differs
    from `scope` (e.g. an explicit foreign repo id): the name tag must never
    claim a repo the script wasn't written in. Duplicates curate/tags.py's
    two-statement tag upsert on purpose (flagged in ABSTRACTIONS.md); caller
    commits."""
    assert script_id > 0, "stamp_repo_tag needs a persisted script id"
    assert SCOPE_RE.match(scope), "stamp_repo_tag takes an already-resolved scope"
    identity = repo_identity()
    if identity is None or identity[0] != scope:
        return None
    tag = identity[1]
    conn.execute("INSERT OR IGNORE INTO tag (name) VALUES (?)", (tag,))
    conn.execute(
        "INSERT OR IGNORE INTO script_tag (script_id, tag_id) SELECT ?, id FROM tag WHERE name = ?",
        (script_id, tag),
    )
    return tag


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
