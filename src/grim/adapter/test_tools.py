"""Tests for adapter/tools.py — the tool schemas and the pure
tool-call → cli.main argv mapper. No mini-swe-agent import needed."""

from __future__ import annotations

import pytest

from grim.adapter.tools import GRIM_TOOLS, SUBMIT_TOOL_NAME, tool_call_to_argv


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
