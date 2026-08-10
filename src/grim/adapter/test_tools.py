"""Tests for adapter/tools.py — the tool schemas and the pure
tool-call → cli.main argv mapper. No mini-swe-agent import needed."""

from __future__ import annotations

import shlex

import pytest

from grim.adapter.tools import (
    GRIM_TOOLS,
    SUBMIT_TOOL_NAME,
    lang_enum,
    render_command,
    tool_call_to_argv,
)


def test_lang_enum_defaults_and_extended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_BASE_LANGUAGES", raising=False)
    monkeypatch.delenv("GRIM_LANGUAGES", raising=False)
    assert lang_enum() == ["bash", "python"]
    monkeypatch.setenv("GRIM_LANGUAGES", "jq")
    assert lang_enum() == ["bash", "jq", "python"]


def test_lang_enum_solo_arms_and_fail_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Solo arm: no builtins, one extended language — schema and prompt name
    # only it; jq,bash keeps just that pair.
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "")
    monkeypatch.setenv("GRIM_LANGUAGES", "jq")
    assert lang_enum() == ["jq"]
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "bash")
    assert lang_enum() == ["bash", "jq"]
    # Both knobs emptied -> builtin pair, mirroring dispatch's fail-safe.
    monkeypatch.setenv("GRIM_LANGUAGES", "")
    monkeypatch.setenv("GRIM_BASE_LANGUAGES", "")
    assert lang_enum() == ["bash", "python"]


def test_write_maps_flags_and_body_to_stdin() -> None:
    argv, stdin = tool_call_to_argv(
        "write", {"name": "greet", "lang": "python", "desc": "d", "body": "print('hi')"}
    )
    assert argv == ["write", "--name", "greet", "--lang", "python", "--desc", "d"]
    assert stdin == "print('hi')"


def test_write_includes_optional_parent_and_scope() -> None:
    argv, _ = tool_call_to_argv(
        "write",
        {"name": "g", "lang": "bash", "desc": "d", "body": "x", "parent": "base", "scope": "repo"},
    )
    assert "--parent" in argv and argv[argv.index("--parent") + 1] == "base"
    assert "--scope" in argv and argv[argv.index("--scope") + 1] == "repo"


def test_update_requires_changelog_and_sends_body_on_stdin() -> None:
    argv, stdin = tool_call_to_argv("update", {"name": "greet", "changelog": "fix", "body": "new"})
    assert argv == ["update", "greet", "--changelog", "fix"]
    assert stdin == "new"


def test_read_by_name_and_by_exec_page() -> None:
    assert tool_call_to_argv("read", {"name": "greet"}) == (["read", "greet"], None)
    argv, _ = tool_call_to_argv("read", {"exec": 12, "page": 2})
    assert argv == ["read", "--exec", "12", "--page", "2"]  # ints stringified for argparse


def test_find_and_list_optionals() -> None:
    assert tool_call_to_argv("find", {"query": "github repos"}) == (
        ["find", "github repos"],
        None,
    )
    argv, _ = tool_call_to_argv("list", {"lang": "python", "limit": 5})
    assert argv == ["list", "--lang", "python", "--limit", "5"]


def test_run_places_script_args_after_double_dash_and_feeds_stdin() -> None:
    argv, stdin = tool_call_to_argv(
        "run", {"name": "lister", "args": ["aaronmyatt"], "timeout": 30, "stdin": "in"}
    )
    assert argv == ["run", "lister", "--timeout", "30", "--", "aaronmyatt"]
    assert stdin == "in"


def test_run_head_tail_limit_flags() -> None:
    argv, _ = tool_call_to_argv("run", {"name": "big", "head": 40, "tail": 10})
    assert argv == ["run", "big", "--head", "40", "--tail", "10"]


def test_submit_is_not_a_data_verb_and_is_rejected_by_the_mapper() -> None:
    # submit never reaches cli.main — environment.py stops on it. Mapping it
    # is a programming error, guarded by the internal assertion.
    with pytest.raises(AssertionError):
        tool_call_to_argv(SUBMIT_TOOL_NAME, {"result": "done"})


def test_grim_tools_expose_six_verbs_plus_submit() -> None:
    names = [t["function"]["name"] for t in GRIM_TOOLS]
    assert names == ["write", "update", "read", "list", "find", "run", "submit"]
    # every schema is a well-formed function tool
    for tool in GRIM_TOOLS:
        assert tool["type"] == "function"
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        # required keys must be declared as properties
        assert set(params["required"]) <= set(params["properties"])


def test_submit_schema_requires_a_result() -> None:
    submit = next(t for t in GRIM_TOOLS if t["function"]["name"] == SUBMIT_TOOL_NAME)
    assert submit["function"]["parameters"]["required"] == ["result"]


def test_render_command_gives_a_readable_cli_string() -> None:
    # Actions must carry a display `command` for mini's InteractiveAgent.
    assert render_command("find", {"query": "github repos"}) == "grim find 'github repos'"
    assert render_command("run", {"name": "greet", "args": ["a b"]}) == "grim run greet -- 'a b'"
    assert render_command(SUBMIT_TOOL_NAME, {"result": "done"}) == "submit"


def test_argv_elements_are_always_strings_even_for_wrong_typed_input() -> None:
    # A model may slip a list into a string field; the mapper must coerce
    # every argv element through _str so render_command's shlex.join can
    # never raise TypeError (regression for the mini-swe-agent
    # 'expected string object, got list' crash).
    argv, _ = tool_call_to_argv("find", {"query": ["a", "b"]})
    assert argv == ["find", "['a', 'b']"]
    assert all(isinstance(a, str) for a in argv)
    argv, _ = tool_call_to_argv("write", {"name": ["x"], "lang": "bash", "desc": "d", "body": "b"})
    assert argv == ["write", "--name", "['x']", "--lang", "bash", "--desc", "d"]
    argv, _ = tool_call_to_argv("run", {"name": "greet", "args": "hi there"})
    assert argv == ["run", "greet", "--", "hi there"]


def test_render_command_tolerates_list_valued_string_fields() -> None:
    # render_command must always produce a display string, never crash.
    assert render_command("find", {"query": ["a", "b"]}) == "grim find " + shlex.join(
        ["['a', 'b']"]
    )
