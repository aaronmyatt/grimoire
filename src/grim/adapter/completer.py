"""Interactive `@`/`:` completion for grim-agent's prompt (Phase 1 of the
@-command plan; `!` execute-and-substitute is Phase 2, in bang.py).

Attaches a prompt_toolkit completer to mini's own prompt sessions so a human
composing a task/comment can fuzzy-find library scripts (and files) — no fork
of mini, just its existing sessions configured:

- ``@token`` -> files only, fuzzy-matched per path segment (``@rdme`` finds
  ``README.md``), uncapped
- ``:token`` -> library scripts only, matched through the same FTS5 index
  `grim find` searches (prefix tokens over name/description/body, bm25-ranked
  with find's column weights), uncapped; bare ``:`` lists the library

The completed text is a plain mention; grimoire.yaml's system_template teaches
the agent that ``:slug`` is a library script (read/run it) and ``@path`` is a
file (read_file it). Attended-terminal
only — a no-op where there's no TTY (containers/cron).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    FuzzyCompleter,
    PathCompleter,
)
from prompt_toolkit.document import Document

from grim import db

_TRIGGERS = ("@", ":")
# FuzzyCompleter turns the typed segment into a subsequence match over what
# PathCompleter lists ("@rdme" -> README.md); the custom pattern extends the
# fuzzy word to the whole path segment so dots and dashes fuzz too, while "/"
# still delimits segments (directory prefixes complete level by level).
# Uncapped by explicit human decision — the bound is implicit: PathCompleter
# only ever lists a single directory's entries.
# Ref: https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html#fuzzy-completion
_PATH_COMPLETER = FuzzyCompleter(PathCompleter(expanduser=True), pattern=r"^[^/\s]*")


def _current_token(before: str) -> str:
    """The whitespace-delimited token immediately before the cursor, or '' when
    the cursor follows whitespace (nothing to complete)."""
    assert isinstance(before, str), "text before cursor is a string"
    if not before or before[-1].isspace():
        return ""
    return before.rsplit(maxsplit=1)[-1]


def _fts_prefix_query(text: str) -> str:
    """Tokenize into an OR'd FTS5 prefix query (`"rea"* OR "fi"*`) — the
    completion-flavored sibling of agent.py's _match_query (both are
    adapter-owned duplicates of verbs/_shared.py's fts_match_query; slices
    don't import each other's internals, root CLAUDE.md §2). Splitting on
    non-alphanumerics mirrors FTS5's unicode61 tokenizer, so a typed
    `read_f` still reaches the indexed `read` + `f` tokens.
    Ref: https://sqlite.org/fts5.html#fts5_prefix_queries"""
    assert isinstance(text, str), "text is a string"
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    return " OR ".join(f'"{t}"*' for t in tokens)


def _script_rows(query: str) -> list[tuple[str, str]]:
    """(name, description) for non-archived scripts, fuzzy-matched through the
    same FTS5 index `grim find` searches — prefix tokens over
    name/description/body, ranked by bm25 with find's column weighting
    (name > description > body; bm25 is more-negative-is-better, so ascending
    order puts the closest first — https://sqlite.org/fts5.html
    #the_bm25_function). A tokenless query (bare `:`) lists the whole library.
    Uncapped by explicit human decision — the implicit bound is the library's
    non-archived script count. Empty (never raises) if the DB is absent or
    unmigrated — completion must never crash the prompt."""
    assert isinstance(query, str), "query is a string"
    path = db.resolve_db_path()
    if not path.exists():  # avoid materializing an empty DB just to complete
        return []
    match_query = _fts_prefix_query(query)
    conn = db.connect(path)
    try:
        if match_query:
            rows = conn.execute(
                "SELECT s.name, s.description FROM script_fts "
                "JOIN script s ON s.id = script_fts.rowid "
                "WHERE script_fts MATCH ? AND s.archived = 0 "
                "ORDER BY bm25(script_fts, 10.0, 5.0, 1.0), s.name",
                (match_query,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, description FROM script WHERE archived = 0 ORDER BY name"
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
    """Delegate to the fuzzy path completer over just the path part, then
    re-tag each result as a file."""
    sub = Document(text=query, cursor_position=len(query))
    for comp in _PATH_COMPLETER.get_completions(sub, complete_event):
        yield Completion(
            comp.text, start_position=comp.start_position, display=comp.display, display_meta="file"
        )


class GrimCompleter(Completer):
    """`@` = files only (fuzzy), `:` = scripts only (FTS5-ranked). See the
    module docstring."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        token = _current_token(document.text_before_cursor)
        assert isinstance(token, str), "token is a string"
        if not token or token[0] not in _TRIGGERS:
            return
        query = token[1:]
        if token[0] == "@":  # ':' is scripts-only
            yield from _file_completions(query, complete_event)
        else:
            yield from _script_completions(query)


def install_grim_completer(completer: Completer | None = None) -> None:
    """Attach the completer to mini's prompt_toolkit sessions so `@`/`:`
    completion works wherever the human types (initial task, follow-up prompt,
    interrupt comment). Mutating mini's own sessions keeps this additive — no
    fork. Safe without a TTY: prompt_toolkit simply shows nothing."""
    from minisweagent.agents.utils import prompt_user  # noqa: PLC0415 -- extra may be absent

    completer = completer or GrimCompleter()
    # The documented input seam (prompt_user.py): single-line follow-up,
    # multiline /m comment, and — once grim's self-heal has applied the
    # submit-on-Enter patch — the initial task prompt session too.
    sessions = [prompt_user.prompt_session, prompt_user._multiline_prompt_session]
    task_session = getattr(prompt_user, "_task_prompt_session", None)
    if task_session is not None:
        sessions.append(task_session)
    for session in sessions:
        session.completer = completer
        session.complete_while_typing = True
    assert prompt_user.prompt_session.completer is completer, "completer attached to the prompt"
