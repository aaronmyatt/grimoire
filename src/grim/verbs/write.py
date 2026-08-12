"""`grim write` — create a new script + its first version.

Never skips slug lint, mandatory description, or syntax lint
(verbs/CLAUDE.md invariant) — the anti-duplication mechanism build plan
§4/§6 depend on.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from grim.exec import dispatch
from grim.verbs import _shared


@dataclass(frozen=True)
class WriteRequest:
    name: str
    language: str
    description: str
    body: str
    parent: str | None
    scope: str | None
    session_id: str


@dataclass(frozen=True)
class WriteResult:
    script_id: int
    version_id: int
    version: int
    similar: list[tuple[str, float]]


def write_script(
    conn: sqlite3.Connection, request: WriteRequest, *, enforce_language_gate: bool = True
) -> WriteResult:
    """Validate and insert a new script + version 1.

    `enforce_language_gate=False` exists for human-initiated seeding
    (`grim init` via seeds/loader.py): the env-derived writable set
    (GRIM_LANGUAGES / GRIM_BASE_LANGUAGES) is an agent-experiment knob and
    must not decide what the stdlib contains. Ungated writes still stay
    inside the full runner catalog, and the toggles keep gating writing
    only, never execution (docs/languages.md).
    """
    _shared.validate_slug(request.name)
    if not request.description.strip():
        raise ValueError("description is required")
    if enforce_language_gate and request.language not in dispatch.supported_languages():
        supported = ", ".join(sorted(dispatch.supported_languages()))
        raise ValueError(f"unsupported language {request.language!r} — supported: {supported}")
    # language_tool() returns '' for a language outside the runner catalog
    # (exec/dispatch.py) — the floor an UNGATED write still obeys: seeding
    # bypasses the writable-set knob, never the catalog.
    if not dispatch.language_tool(request.language):
        raise ValueError(f"unknown language {request.language!r} — not in the runner catalog")
    lint_error = _shared.lint(request.language, request.body)
    if lint_error:
        raise ValueError(lint_error)

    similar = _shared.similar_scripts(conn, f"{request.name} {request.description}")
    scope = request.scope or _shared.default_scope()
    _shared.ensure_session(conn, request.session_id)

    parent_version_id = None
    if request.parent is not None:
        parent_name, parent_version = _shared.parse_name_version(request.parent)
        parent_version_id = _shared.resolve_script_version(conn, parent_name, parent_version)["id"]

    try:
        conn.execute(
            "INSERT INTO script (name, language, description, scope, "
            "parent_version_id, origin_session_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                request.name,
                request.language,
                request.description,
                scope,
                parent_version_id,
                request.session_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"script {request.name!r} already exists — use 'grim update' to add a version"
        ) from exc

    script_id = conn.execute("SELECT id FROM script WHERE name = ?", (request.name,)).fetchone()[
        "id"
    ]
    cursor = conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash) VALUES (?, 1, ?, ?)",
        (script_id, request.body, _shared.body_hash(request.body)),
    )
    conn.commit()

    assert script_id > 0, "inserted script must get a positive id"
    assert cursor.lastrowid is not None, "script_version insert must produce a rowid"
    return WriteResult(script_id=script_id, version_id=cursor.lastrowid, version=1, similar=similar)


def cmd_write(args: argparse.Namespace) -> int:
    body = sys.stdin.read()
    conn = _shared.connect()
    try:
        request = WriteRequest(
            name=args.name,
            language=args.lang,
            description=args.desc,
            body=body,
            parent=args.parent,
            scope=args.scope,
            session_id=_shared.session_id_from_env(),
        )
        try:
            result = write_script(conn, request)
        except (ValueError, LookupError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        print(f"wrote {args.name}@{result.version}")
        for name, score in result.similar:
            print(f"similar: {name} ({score:.2f}) — consider 'grim update' or '--parent'")
        return 0
    finally:
        conn.close()
