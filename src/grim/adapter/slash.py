"""`/verb args...` — mirrors grim's own CLI subcommands as slash commands in
grim-agent's interactive prompt, alongside mini-swe-agent's own /h /m /y /c
/u (see agent.py's GrimAgent._prompt_and_handle_slash_commands). Dispatches
directly, bypassing the model entirely — a human side-channel, the same
category as mini's own /h: never added to the conversation, never billed as
tokens.
"""

from __future__ import annotations

import shlex

from grim.adapter.environment import _invoke

# Mirrors cli.py's build_parser() subcommand set. cli.py is a frozen path
# (root CLAUDE.md §5) that changes rarely, so this is a small, intentional
# duplicate of its verb names — same convention as tools.py's _DATA_VERBS.
GRIM_CLI_VERBS = (
    "init",
    "config",
    "doctor",
    "near",
    "recent",
    "edit",
    "tag",
    "untag",
    "tags",
    "tagged",
    "favourite",
    "unfavourite",
    "favourites",
    "write",
    "update",
    "read",
    "list",
    "find",
    "run",
)


def parse_slash_command(text: str) -> tuple[str, list[str]] | None:
    """(verb, argv) for a recognized `/verb ...` grim CLI mirror, or None
    when `text` isn't `/`-prefixed, isn't a known grim verb (e.g. mini's own
    /h, /m, /y, /c, /u, or grim-agent's own /new), or fails to shlex-split
    (unbalanced quotes)."""
    assert isinstance(text, str), "text is a string"
    if not text.startswith("/"):
        return None
    try:
        tokens = shlex.split(text[1:])
    except ValueError:
        return None
    if not tokens or tokens[0] not in GRIM_CLI_VERBS:
        return None
    return tokens[0], tokens[1:]


def run_slash_command(text: str, session_id: str) -> str | None:
    """Execute a recognized grim CLI slash command in-process and return its
    captured output, or None when `text` isn't one (the caller should treat
    it as ordinary input instead). Same in-process cli.main dispatch as
    bang.py's !slug and the agent's own tool calls use — no new subprocess."""
    parsed = parse_slash_command(text)
    if parsed is None:
        return None
    assert isinstance(session_id, str) and session_id, "session_id is a non-empty string"
    verb, rest = parsed
    output, _ = _invoke([verb, *rest], "", session_id)
    assert isinstance(output, str), "invoke always returns captured text"
    return output
