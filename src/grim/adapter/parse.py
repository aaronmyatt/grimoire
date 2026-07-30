"""Parses one fenced code block's content into a grim invocation — the
enforcement point build plan D6/D7 describe as "one fenced code block =
one grim command; script bodies pass via heredoc". Pure text in, pure
data out: no I/O, no mini-swe-agent import.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

SIX_VERBS = frozenset({"write", "update", "read", "list", "find", "run"})

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")


@dataclass(frozen=True)
class ParsedCommand:
    verb: str
    argv: list[str]  # includes the verb, e.g. ["write", "--name", ...] — ready for parse_args()
    stdin: str  # heredoc body, "" if none


def _extract_heredoc(
    command_line: str, remaining_lines: list[str]
) -> tuple[str, str | None] | None:
    """Returns (stripped_command_line, body) if `command_line` ends in a
    heredoc redirect and its terminator is found in `remaining_lines`,
    None if the redirect is malformed or unterminated, or
    (command_line, None) unchanged if there's no redirect at all.
    """
    match = _HEREDOC_RE.search(command_line)
    if match is None:
        return command_line, None
    delimiter = match.group(2)
    body_lines: list[str] = []
    for line in remaining_lines:
        if line.strip() == delimiter:
            return command_line[: match.start()].rstrip(), "\n".join(body_lines)
        body_lines.append(line)
    return None


def parse_grim(text: str) -> ParsedCommand | None:
    """Parse a model action's raw text into a ParsedCommand, or None if
    it isn't a `[grim] <verb> ...` invocation (malformed heredoc,
    unbalanced quotes, or a verb outside the six-verb set — D12's
    human-only verbs like `init` are rejected here too). The leading
    "grim" is optional — see the tolerance note below."""
    assert isinstance(text, str), "parse_grim expects the model's raw block content as text"
    lines = text.splitlines()
    cmd_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if cmd_idx is None:
        return None

    extracted = _extract_heredoc(lines[cmd_idx], lines[cmd_idx + 1 :])
    if extracted is None:
        return None
    command_line, stdin = extracted

    try:
        argv = shlex.split(command_line)
    except ValueError:
        return None
    if not argv:
        return None

    # Tolerate the model treating the ```grim fence tag as already saying
    # "grim" and dropping the literal word from the content — a natural
    # mistake, since every other language-tagged fence works exactly that
    # way (you never repeat "python" inside a ```python block). Both
    # "grim <verb> ..." and bare "<verb> ..." are accepted; the verb
    # whitelist below is unchanged either way.
    if argv[0] == "grim":
        argv = argv[1:]
    if not argv or argv[0] not in SIX_VERBS:
        return None

    assert argv[0] in SIX_VERBS, "verb must be one of the six agent-facing verbs"
    return ParsedCommand(verb=argv[0], argv=argv, stdin=stdin or "")
