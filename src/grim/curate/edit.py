"""`grim edit NAME` — round-trip a script's body through $EDITOR and persist
the result as a new script_version, with an AI-generated (or manual)
changelog. Human-only (curate/CLAUDE.md): never wired into
adapter/tools.py::GRIM_TOOLS, so an LLM can never drive this itself — only a
human, whether at a real shell or through grim-agent's /edit slash command
(adapter/slash.py), which reaches this exact function via the same
in-process cli.main dispatch every other /verb command uses.
"""

from __future__ import annotations

import argparse
import difflib
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grim.curate import _shared

# POSIX fallback when $EDITOR is unset — the same default `git commit` uses.
# Ref: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/vi.html
DEFAULT_EDITOR = "vi"

_LANGUAGE_SUFFIX = {"python": ".py", "bash": ".sh"}

# A real network call, so it must not hang `grim edit` indefinitely.
_AI_TIMEOUT_S = 15.0

_AI_CHANGELOG_PROMPT = (
    "Below is a unified diff of a script's changes. Reply with ONE short, "
    "present-tense changelog line describing what changed (like a git commit "
    "subject) — no prefix, no quotes, nothing else.\n\n{diff}"
)

_GENERIC_CHANGELOG = "edited via grim edit"


@dataclass(frozen=True)
class EditResult:
    script_id: int
    version: int
    changelog: str


def edit_in_editor(body: str, language: str, *, editor: str) -> str:
    """Round-trips `body` through `editor` via a real subprocess with
    inherited stdio (a genuine interactive session, not `_invoke`'s captured
    dispatch) — returns the temp file's contents after the editor exits.
    Always cleans up the temp file, even if the editor errors."""
    assert isinstance(body, str), "body is a string"
    assert editor.strip(), "editor command must not be blank"
    suffix = _LANGUAGE_SUFFIX.get(language, ".txt")
    fd, raw_path = tempfile.mkstemp(prefix="grim-edit-", suffix=suffix)
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
        subprocess.run([*shlex.split(editor), str(path)], check=False)
        result = path.read_text()
    finally:
        path.unlink(missing_ok=True)
    assert isinstance(result, str), "the edited body is always read back as text"
    return result


def unified_diff(old: str, new: str) -> str:
    """Pure unified diff between the old and new body, for the changelog
    prompt — no filesystem, no network."""
    assert isinstance(old, str) and isinstance(new, str), "diff inputs are strings"
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), lineterm=""
    )
    return "".join(lines)


def _default_complete(diff: str, model: str) -> str:
    import litellm  # noqa: PLC0415 -- optional `agent` extra; edit degrades without it

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": _AI_CHANGELOG_PROMPT.format(diff=diff)}],
        timeout=_AI_TIMEOUT_S,
    )
    return str(response.choices[0].message.content)


def ai_changelog(
    diff: str, model: str | None, *, complete: Callable[[str, str], str] = _default_complete
) -> str | None:
    """One-line changelog via `complete`, or None if no model is configured
    or the call fails for any reason — litellm only ships under the
    optional `agent` extra, and this must never crash `grim edit`."""
    assert isinstance(diff, str), "diff is a string"
    if not model or not diff.strip():
        return None
    try:
        text = complete(diff, model)
    except Exception:  # noqa: BLE001 -- any AI-call failure degrades, never crashes edit
        return None
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return line or None


def changelog_model() -> str | None:
    """GRIM_CHANGELOG_MODEL, falling back to GRIM_MODEL (config.py seeds
    both from ~/.grimoire/config.toml); None if neither is set."""
    return os.environ.get("GRIM_CHANGELOG_MODEL") or os.environ.get("GRIM_MODEL")


@dataclass(frozen=True)
class ChangelogOptions:
    override: str | None
    model: str | None
    ai_fn: Callable[[str, str | None], str | None] = ai_changelog
    prompt_fn: Callable[[str], str] = input


def resolve_changelog(diff: str, options: ChangelogOptions) -> str:
    """override -> AI -> one manual prompt -> generic fallback. Never blocks
    longer than a single prompt attempt, never raises."""
    assert isinstance(diff, str), "diff is a string"
    if options.override and options.override.strip():
        return options.override.strip()
    generated = options.ai_fn(diff, options.model)
    if generated:
        return generated
    try:
        typed = options.prompt_fn("changelog: ").strip()
    except (EOFError, OSError):
        typed = ""
    return typed or _GENERIC_CHANGELOG


@dataclass(frozen=True)
class PersistRequest:
    script_id: int
    latest_version: int
    language: str
    body: str
    changelog: str


def persist_edit(conn: sqlite3.Connection, request: PersistRequest) -> EditResult:
    """Same lint-then-insert-script_version logic as verbs/update.py's
    update_script — a deliberate duplicate (curate never imports verbs/,
    curate/CLAUDE.md)."""
    lint_error = _shared.lint(request.language, request.body)
    if lint_error:
        raise ValueError(lint_error)
    new_version = request.latest_version + 1
    cursor = conn.execute(
        "INSERT INTO script_version (script_id, version, body, body_hash, changelog) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            request.script_id,
            new_version,
            request.body,
            _shared.body_hash(request.body),
            request.changelog,
        ),
    )
    conn.commit()
    assert cursor.lastrowid is not None, "script_version insert must produce a rowid"
    assert new_version > request.latest_version, "persist_edit must always increment the version"
    return EditResult(script_id=request.script_id, version=new_version, changelog=request.changelog)


def cmd_edit(args: argparse.Namespace) -> int:
    conn = _shared.connect()
    try:
        try:
            current = _shared.resolve_script_version(conn, args.name, None)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        editor = os.environ.get("EDITOR", DEFAULT_EDITOR)
        new_body = edit_in_editor(current["body"], current["language"], editor=editor)
        if new_body == current["body"]:
            print("no changes")
            return 0

        diff = unified_diff(current["body"], new_body)
        options = ChangelogOptions(override=args.changelog, model=changelog_model())
        changelog = resolve_changelog(diff, options)
        request = PersistRequest(
            script_id=current["script_id"],
            latest_version=current["version"],
            language=current["language"],
            body=new_body,
            changelog=changelog,
        )
        try:
            result = persist_edit(conn, request)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"updated {args.name}@{result.version} — {result.changelog}")
        return 0
    finally:
        conn.close()
