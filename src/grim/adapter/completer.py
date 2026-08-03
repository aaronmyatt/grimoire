"""Interactive `@`/`:` completion for grim-agent's prompt (Phase 1 of the
@-command plan; `!` execute-and-substitute is a deferred Phase 2).

Attaches a prompt_toolkit completer to mini's own prompt sessions so a human
composing a task/comment can fuzzy-find library scripts (and files) — no fork
of mini, just its existing sessions configured:

- ``@token`` -> library scripts **and** files (labeled, scripts first)
- ``:token`` -> library scripts only (no file noise)

The completed text is a plain mention; grimoire.yaml's system_template teaches
the agent that ``@slug``/``:slug`` is a library script (read/run it) and
``@path`` (a slash or extension) is a file (read_file it). Attended-terminal
only — a no-op where there's no TTY (containers/cron).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document

from grim import db

_SCRIPT_LIMIT = 10  # bounded lookup per keystroke (CLAUDE.md §1: explicit max)
_FILE_LIMIT = 10  # cap files so they can't bury the scripts in the dropdown
_TRIGGERS = ("@", ":")
_PATH_COMPLETER = PathCompleter(expanduser=True)


def _current_token(before: str) -> str:
    """The whitespace-delimited token immediately before the cursor, or '' when
    the cursor follows whitespace (nothing to complete)."""
    assert isinstance(before, str), "text before cursor is a string"
    if not before or before[-1].isspace():
        return ""
    return before.rsplit(maxsplit=1)[-1]


def _script_rows(query: str) -> list[tuple[str, str]]:
    """(name, description) for non-archived scripts whose name starts with
    `query`. Empty (never raises) if the DB is absent or unmigrated —
    completion must never crash the prompt."""
    assert isinstance(query, str), "query is a string"
    path = db.resolve_db_path()
    if not path.exists():  # avoid materializing an empty DB just to complete
        return []
    conn = db.connect(path)
    try:
        rows = conn.execute(
            "SELECT name, description FROM script WHERE name LIKE ? AND archived = 0 "
            "ORDER BY name LIMIT ?",
            (query.replace("%", "") + "%", _SCRIPT_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [(row[0], row[1]) for row in rows]


def _script_completions(query: str) -> Iterable[Completion]:
    for name, desc in _script_rows(query):
        yield Completion(
            name, start_position=-len(query), display=name, display_meta=f"script · {desc}"
        )


def _file_completions(query: str, complete_event: CompleteEvent) -> Iterable[Completion]:
    """Delegate to prompt_toolkit's PathCompleter over just the path part, then
    re-tag each result as a file and cap the count."""
    sub = Document(text=query, cursor_position=len(query))
    for count, comp in enumerate(_PATH_COMPLETER.get_completions(sub, complete_event)):
        if count >= _FILE_LIMIT:
            break
        yield Completion(
            comp.text, start_position=comp.start_position, display=comp.display, display_meta="file"
        )


class GrimCompleter(Completer):
    """`@` = scripts + files, `:` = scripts only. See the module docstring."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        token = _current_token(document.text_before_cursor)
        assert isinstance(token, str), "token is a string"
        if not token or token[0] not in _TRIGGERS:
            return
        query = token[1:]
        yield from _script_completions(query)
        if token[0] == "@":  # ':' is scripts-only
            yield from _file_completions(query, complete_event)


def install_grim_completer(completer: Completer | None = None) -> None:
    """Attach the completer to mini's two prompt_toolkit sessions so `@`/`:`
    completion works wherever the human types (initial task, follow-up prompt,
    interrupt comment). Mutating mini's own sessions keeps this additive — no
    fork. Safe without a TTY: prompt_toolkit simply shows nothing."""
    from minisweagent.agents.utils import prompt_user  # noqa: PLC0415 -- extra may be absent

    completer = completer or GrimCompleter()
    # _multiline_prompt_session is mini's (underscored) session for the
    # task/follow-up prompt; both are the documented input seam (prompt_user.py).
    for session in (prompt_user.prompt_session, prompt_user._multiline_prompt_session):
        session.completer = completer
        session.complete_while_typing = True
    assert prompt_user.prompt_session.completer is completer, "completer attached to the prompt"
