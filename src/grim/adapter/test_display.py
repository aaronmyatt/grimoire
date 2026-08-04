"""Tests for adapter/display.py — pure renderable helpers, no model, no DB,
no TTY. Rendering is asserted through a recording Console
(https://rich.readthedocs.io/en/stable/console.html#capturing-output), which
is deterministic for a fixed width."""

from __future__ import annotations

from typing import Any

from rich.console import Console

from grim.adapter.display import (
    BODY_PREVIEW_LINES,
    action_renderables,
    body_lexer,
    grim_actions,
    reasoning_text,
    submit_result,
)
from grim.adapter.tools import render_command


def _act(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """An action shaped exactly as toolcall_model._parse_one emits it."""
    return {
        "tool": tool,
        "args": args,
        "tool_call_id": "tc_1",
        "command": render_command(tool, args),
    }


def _assistant(actions: list[dict[str, Any]], content: str | None = None) -> dict[str, Any]:
    return {"role": "assistant", "content": content, "extra": {"actions": actions}}


def _render(renderables: list[object]) -> str:
    console = Console(record=True, width=80)
    for renderable in renderables:
        console.print(renderable)
    return console.export_text()


# --- grim_actions ------------------------------------------------------------


def test_grim_actions_returns_actions_for_wellformed_assistant_message() -> None:
    actions = [_act("find", {"query": "dad jokes"})]
    assert grim_actions(_assistant(actions)) is actions


def test_grim_actions_rejects_other_roles() -> None:
    action = _act("find", {"query": "x"})
    assert grim_actions({"role": "tool", "extra": {"actions": [action]}}) is None
    assert grim_actions({"role": "exit", "content": "", "extra": {"actions": [action]}}) is None


def test_grim_actions_rejects_malformed_shapes() -> None:
    assert grim_actions({"role": "assistant"}) is None
    assert grim_actions({"role": "assistant", "extra": None}) is None
    assert grim_actions({"role": "assistant", "extra": {"actions": "nope"}}) is None
    assert grim_actions({"role": "assistant", "extra": {"actions": []}}) is None
    # Human-mode command messages carry `command` but no tool/args
    # (minisweagent InteractiveAgent.query builds this shape).
    human = {"role": "assistant", "extra": {"actions": [{"command": "ls"}]}}
    assert grim_actions(human) is None
    assert grim_actions({"role": "assistant", "extra": {"actions": [{"tool": "find"}]}}) is None


# --- submit_result -----------------------------------------------------------


def test_submit_result_returns_markdown_for_submit() -> None:
    assert submit_result(_act("submit", {"result": "# Done"})) == "# Done"
    assert submit_result(_act("submit", {"result": ""})) == ""


def test_submit_result_rejects_data_verbs_and_nonstring_results() -> None:
    assert submit_result(_act("find", {"query": "x"})) is None
    nonstring = {"tool": "submit", "args": {"result": 42}, "command": "submit"}
    assert submit_result(nonstring) is None


# --- reasoning_text ----------------------------------------------------------


def test_reasoning_text_strips_tool_calls_without_mutating() -> None:
    message = {
        "role": "assistant",
        "content": "Everything is in place.",
        "tool_calls": [{"function": {"name": "submit", "arguments": '{"result": "# Done"}'}}],
    }
    text = reasoning_text(message)
    assert "Everything is in place." in text
    assert '{"result"' not in text
    assert "tool_calls" in message, "original message left intact"


def test_reasoning_text_handles_none_and_list_content() -> None:
    assert reasoning_text({"role": "assistant", "content": None}) == ""
    listy = {"role": "assistant", "content": [{"type": "text", "text": "thinking"}]}
    assert reasoning_text(listy) == "thinking"


# --- body_lexer --------------------------------------------------------------


def test_body_lexer_honors_lang_then_shebang_then_text() -> None:
    assert body_lexer({"lang": "python", "body": "#!/bin/sh"}) == "python"
    assert body_lexer({"lang": "bash", "body": ""}) == "bash"
    assert body_lexer({"body": "#!/usr/bin/env bash\necho hi"}) == "bash"
    assert body_lexer({"body": "import os"}) == "text"
    assert body_lexer({}) == "text"


# --- action_renderables ------------------------------------------------------


def test_submit_renders_markdown_not_json() -> None:
    out = _render(action_renderables(_act("submit", {"result": "# Done\n\n- item one"})))
    assert "Done" in out
    assert "item one" in out
    assert '{"result"' not in out


def test_data_verb_renders_command_line() -> None:
    out = _render(action_renderables(_act("run", {"name": "gardener", "tail": 20})))
    assert "grim run gardener --tail 20" in out
    assert '{"name"' not in out


def test_write_body_preview_is_truncated() -> None:
    total_lines = BODY_PREVIEW_LINES + 10
    body = "\n".join(f"line_{n}" for n in range(1, total_lines + 1))
    action = _act("write", {"name": "big", "lang": "python", "desc": "d", "body": body})
    out = _render(action_renderables(action))
    assert "grim write" in out
    assert "line_1" in out
    assert f"line_{total_lines}" not in out
    assert "+10 more lines" in out
