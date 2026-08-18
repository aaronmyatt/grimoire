"""`grim update` — append a new script_version. Append-only (D4): never
mutates an existing version.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from grim.exec import dispatch
from grim.verbs import _shared


@dataclass(frozen=True)
class UpdateRequest:
    name: str
    changelog: str
    body: str
    language: str | None = None  # None = keep the current version's language


@dataclass(frozen=True)
class UpdateResult:
    script_id: int
    version_id: int
    version: int
    language: str
    language_changed: bool


def resolve_language(requested: str | None, current: str) -> str:
    """The language this new version is written in: the explicit override, or
    the current version's language when omitted.

    An override is gated exactly as `write` gates a new script — the writable
    set (GRIM_LANGUAGES / GRIM_BASE_LANGUAGES) first, then the runner catalog
    — so `update --lang` can never reach a language `write` would have
    refused. Pure over its two inputs; the env reads live in dispatch.
    """
    assert current, "an existing version always resolves to a language"
    if requested is None:
        return current
    if requested not in dispatch.supported_languages():
        supported = ", ".join(sorted(dispatch.supported_languages()))
        raise ValueError(f"unsupported language {requested!r} — supported: {supported}")
    # language_tool() returns '' outside the runner catalog — the floor that
    # holds even when the writable set has been widened by env.
    if not dispatch.language_tool(requested):
        raise ValueError(f"unknown language {requested!r} — not in the runner catalog")
    assert requested, "a resolved language is never empty"
    return requested


def update_script(conn: sqlite3.Connection, request: UpdateRequest) -> UpdateResult:
    if not request.changelog.strip():
        raise ValueError("changelog is required")
    latest = _shared.resolve_script_version(conn, request.name, None)
    language = resolve_language(request.language, latest["language"])
    lint_error = _shared.lint(language, request.body)
    if lint_error:
        raise ValueError(lint_error)

    new_version = latest["version"] + 1
    cursor = conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash, changelog, language) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            latest["script_id"],
            new_version,
            request.body,
            _shared.body_hash(request.body),
            request.changelog,
            language,
        ),
    )
    language_changed = language != latest["language"]
    if language_changed:
        # script.language answers "what language is this script NOW" — find,
        # list and write's gate read it. Earlier versions keep their own
        # language on their own rows, so `run name@N` stays dispatchable.
        conn.execute("UPDATE script SET language = ? WHERE id = ?", (language, latest["script_id"]))
    conn.commit()

    assert cursor.lastrowid is not None, "script_version insert must produce a rowid"
    assert new_version > latest["version"], "update must always increment the version"
    return UpdateResult(
        script_id=latest["script_id"],
        version_id=cursor.lastrowid,
        version=new_version,
        language=language,
        language_changed=language_changed,
    )


def cmd_update(args: argparse.Namespace) -> int:
    body = sys.stdin.read()
    conn = _shared.connect()
    try:
        request = UpdateRequest(
            name=args.name, changelog=args.changelog, body=body, language=args.lang
        )
        try:
            result = update_script(conn, request)
        except (ValueError, LookupError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"updated {args.name}@{result.version}")
        # Surfaced because it is the one update that changes how every later
        # run of this script is executed — never let it happen silently.
        if result.language_changed:
            print(f"language is now {result.language} (earlier versions keep theirs)")
        return 0
    finally:
        conn.close()
