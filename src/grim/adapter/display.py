"""Rich console rendering of the agent's grim tool calls — replaces mini's
raw-JSON ``_format_tool_call`` fallback (minisweagent/models/utils/
content_string.py, which only special-cases a ``command`` arg key) for
assistant turns. Display only: history, actions, confirm-mode matching, and
the trajectory are untouched — ``GrimAgent.add_messages`` records the
verbatim message dicts and consults this module purely for what to print.

Pure helpers over message/action dicts, so every branch is unit-testable
without a model, a TTY, or the DB (test_display.py).
"""

from __future__ import annotations

from typing import Any

from minisweagent.models.utils.content_string import get_content_string

# Rich renderables: console.print() accepts these objects directly.
# Ref: https://rich.readthedocs.io/en/stable/markdown.html
# Ref: https://rich.readthedocs.io/en/stable/syntax.html
# Ref: https://rich.readthedocs.io/en/stable/reference/rule.html
from rich.markdown import Markdown

# escape() neutralizes [square-bracket] markup in model-derived text so a
# script body or arg can't inject console styling.
# Ref: https://rich.readthedocs.io/en/stable/markup.html#escaping
from rich.markup import escape
from rich.rule import Rule
from rich.syntax import Syntax

from grim.adapter.tools import SUBMIT_TOOL_NAME

# Cap on the write/update body preview — the full body always lives in the
# trajectory and the library, so the console shows a bounded excerpt (§1:
# every collection has an explicit max).
BODY_PREVIEW_LINES = 30

# Postcondition bounds: a body preview is at most a Syntax block plus a
# truncation note; an action is at most a command line plus that preview.
_BODY_RENDERABLES_MAX = 2
_ACTION_RENDERABLES_MAX = 1 + _BODY_RENDERABLES_MAX


def grim_actions(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The message's validated grim actions (``{tool, args, command}`` dicts,
    the shape toolcall_model._parse_one attaches), or None when this message
    must fall back to mini's default rendering. Model output is external
    input: shapes are checked with isinstance, never asserted (§3). The
    missing-``tool`` check also excludes InteractiveAgent's human-mode
    command messages, which carry ``command`` but no ``tool``/``args``."""
    if message.get("role") != "assistant":
        return None
    extra = message.get("extra")
    if not isinstance(extra, dict) or not isinstance(extra.get("actions"), list):
        return None
    actions: list[Any] = extra["actions"]
    shapes_ok = all(
        isinstance(action, dict)
        and isinstance(action.get("tool"), str)
        and isinstance(action.get("args"), dict)
        and isinstance(action.get("command"), str)
        for action in actions
    )
    result = actions if (actions and shapes_ok) else None
    assert result is None or len(result) == len(actions), "actions pass through whole"
    assert result is None or result is actions, "actions are never copied or reordered"
    return result


def submit_result(action: dict[str, Any]) -> str | None:
    """The submit call's result markdown, or None for any other action.
    ``result``'s *presence* is enforced upstream (toolcall_model) but its
    type is not — a non-string result degrades to the command-line path."""
    assert isinstance(action, dict), "callers pass validated action dicts"
    if action.get("tool") != SUBMIT_TOOL_NAME:
        return None
    result = action.get("args", {}).get("result")
    assert result is None or action["tool"] == SUBMIT_TOOL_NAME, "only submit yields a result"
    return result if isinstance(result, str) else None


def reasoning_text(message: dict[str, Any]) -> str:
    """The turn's reasoning text only: a shallow copy without ``tool_calls``,
    so get_content_string never reaches its raw-JSON tool-call fence — the
    actions render separately via action_renderables."""
    stripped = {key: value for key, value in message.items() if key != "tool_calls"}
    assert "tool_calls" not in stripped, "tool calls never render via the fallback"
    assert stripped is not message, "the original message is never mutated"
    return get_content_string(stripped)


def body_lexer(args: dict[str, Any]) -> str:
    """Pygments lexer name for a script body: ``write`` carries
    ``args['lang']``; ``update`` doesn't, so sniff the shebang line,
    defaulting to plain text. Lexer names per
    https://pygments.org/docs/lexers/ (``python``, ``bash``, ``text``)."""
    assert isinstance(args, dict), "callers pass validated args dicts"
    lang = str(args.get("lang", ""))
    if lang in ("python", "bash"):
        return lang
    first_line = str(args.get("body", "")).split("\n", 1)[0]
    lexer = "bash" if first_line.startswith("#!") and "sh" in first_line else "text"
    assert lexer in ("python", "bash", "text"), "lexer is one of the known names"
    return lexer


def _body_renderables(args: dict[str, Any]) -> list[object]:
    """Truncated Syntax preview of a script body travelling via stdin
    (write/update) — empty when there is no body to show."""
    assert isinstance(args, dict), "callers pass validated args dicts"
    body = args.get("body")
    if not isinstance(body, str) or not body:
        return []
    lines = body.split("\n")
    preview = "\n".join(lines[:BODY_PREVIEW_LINES])
    # background_color="default" keeps the terminal's own background instead
    # of Syntax's themed block. Ref: https://rich.readthedocs.io/en/stable/syntax.html
    renderables: list[object] = [Syntax(preview, body_lexer(args), background_color="default")]
    if len(lines) > BODY_PREVIEW_LINES:
        renderables.append(f"[dim]… +{len(lines) - BODY_PREVIEW_LINES} more lines[/dim]")
    assert len(renderables) <= _BODY_RENDERABLES_MAX, "body preview stays bounded"
    return renderables


def action_renderables(action: dict[str, Any]) -> list[object]:
    """Rich renderables for one grim action: the submit result as a Rule +
    Markdown; any data verb as its render_command line (already attached as
    ``action['command']``), plus a truncated Syntax preview when a script
    body travels via stdin (write/update)."""
    result = submit_result(action)
    if result is not None:
        return [Rule("[bold cyan]result[/bold cyan]"), Markdown(result)]
    renderables: list[object] = [
        f"[bold cyan]$[/bold cyan] [bold]{escape(action['command'])}[/bold]"
    ]
    renderables += _body_renderables(action["args"])
    assert renderables, "every action renders something"
    assert len(renderables) <= _ACTION_RENDERABLES_MAX, "command line + bounded preview only"
    return renderables
