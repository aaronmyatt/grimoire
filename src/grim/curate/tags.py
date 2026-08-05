"""`grim tag`/`untag`/`tags`/`tagged` — attach and browse free-form labels
on scripts, read from the `tag`/`script_tag` junction (migration 0002).
Human-only (curate/CLAUDE.md): never wired into `adapter/tools.py::GRIM_TOOLS`.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys

from grim.curate import _shared

TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

DEFAULT_TAG_LIMIT = 20


def normalize_tag(raw: str) -> str:
    """Lowercase + validate one tag. Raises ValueError on an empty or
    out-of-shape tag — external input, not an assert."""
    assert isinstance(raw, str), "raw tag is a string"
    tag = raw.strip().lower()
    if not TAG_RE.match(tag):
        raise ValueError(f"invalid tag {raw!r} — must match {TAG_RE.pattern}")
    return tag


def add_tags(conn: sqlite3.Connection, name: str, tags: list[str]) -> list[str]:
    """Attach `tags` to `name`, creating any tag rows that don't exist yet.
    Idempotent — re-attaching an existing tag is a no-op. Raises LookupError
    if `name` is unknown, ValueError if any tag is malformed."""
    row = _shared.resolve_script_version(conn, name, None)
    normalized = [normalize_tag(t) for t in tags]
    for tag in normalized:
        conn.execute("INSERT OR IGNORE INTO tag (name) VALUES (?)", (tag,))
        conn.execute(
            "INSERT OR IGNORE INTO script_tag (script_id, tag_id) "
            "SELECT ?, id FROM tag WHERE name = ?",
            (row["script_id"], tag),
        )
    conn.commit()
    assert len(normalized) == len(tags), "every requested tag is normalized exactly once"
    return normalized


def remove_tags(conn: sqlite3.Connection, name: str, tags: list[str]) -> list[str]:
    """Detach `tags` from `name`. Idempotent — removing a tag that was never
    attached is a no-op. Raises LookupError if `name` is unknown, ValueError
    if any tag is malformed."""
    row = _shared.resolve_script_version(conn, name, None)
    normalized = [normalize_tag(t) for t in tags]
    for tag in normalized:
        conn.execute(
            "DELETE FROM script_tag WHERE script_id = ? "
            "AND tag_id = (SELECT id FROM tag WHERE name = ?)",
            (row["script_id"], tag),
        )
    conn.commit()
    assert len(normalized) == len(tags), "every requested tag is normalized exactly once"
    return normalized


def list_tags(conn: sqlite3.Connection, limit: int = DEFAULT_TAG_LIMIT) -> list[sqlite3.Row]:
    """Every tag with its usage count, most-used first."""
    assert limit > 0, "tag limit must be positive"
    rows = conn.execute(
        "SELECT t.name, COUNT(st.script_id) AS uses FROM tag t "
        "LEFT JOIN script_tag st ON st.tag_id = t.id "
        "GROUP BY t.id ORDER BY uses DESC, t.name LIMIT ?",
        (limit,),
    ).fetchall()
    assert len(rows) <= limit, "list_tags must not exceed the requested limit"
    return rows


def scripts_for_tag(
    conn: sqlite3.Connection, tag: str, limit: int = DEFAULT_TAG_LIMIT
) -> list[sqlite3.Row]:
    """Non-archived scripts carrying `tag`. Raises LookupError if the tag
    doesn't exist — external input (a likely typo), not an assert."""
    assert limit > 0, "tag limit must be positive"
    normalized = normalize_tag(tag)
    exists = conn.execute("SELECT 1 FROM tag WHERE name = ?", (normalized,)).fetchone()
    if exists is None:
        raise LookupError(f"tag {tag!r} not found")
    rows = conn.execute(
        "SELECT s.name, s.language, s.description FROM script s "
        "JOIN script_tag st ON st.script_id = s.id "
        "JOIN tag t ON t.id = st.tag_id "
        "WHERE t.name = ? AND s.archived = 0 ORDER BY s.name LIMIT ?",
        (normalized, limit),
    ).fetchall()
    assert len(rows) <= limit, "scripts_for_tag must not exceed the requested limit"
    return rows


def cmd_tag(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            normalized = add_tags(conn, args.name, args.tags)
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"tagged {args.name}: {', '.join(normalized)}")
        return 0
    finally:
        conn.close()


def cmd_untag(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            normalized = remove_tags(conn, args.name, args.tags)
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"untagged {args.name}: {', '.join(normalized)}")
        return 0
    finally:
        conn.close()


def cmd_tags(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        for row in list_tags(conn, args.limit or DEFAULT_TAG_LIMIT):
            print(f"{row['name']}\tuses={row['uses']}")
        return 0
    finally:
        conn.close()


def cmd_tagged(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            rows = scripts_for_tag(conn, args.tag, args.limit or DEFAULT_TAG_LIMIT)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for row in rows:
            print(f"{row['name']}\t{row['language']}\t{row['description']}")
        return 0
    finally:
        conn.close()


# --- favourite: sugar over tagging with one well-known tag ------------------
# Not a separate `favourite` column — starring a script is exactly tagging it
# with FAVOURITE_TAG, so there is one mechanism for "this script is special."

FAVOURITE_TAG = "favourite"


def cmd_favourite(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            add_tags(conn, args.name, [FAVOURITE_TAG])
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"favourited {args.name}")
        return 0
    finally:
        conn.close()


def cmd_unfavourite(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            remove_tags(conn, args.name, [FAVOURITE_TAG])
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"unfavourited {args.name}")
        return 0
    finally:
        conn.close()


def cmd_favourites(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            rows = scripts_for_tag(conn, FAVOURITE_TAG, args.limit or DEFAULT_TAG_LIMIT)
        except LookupError:
            rows = []  # nobody has favourited anything yet — not an error
        for row in rows:
            print(f"{row['name']}\t{row['language']}\t{row['description']}")
        return 0
    finally:
        conn.close()
