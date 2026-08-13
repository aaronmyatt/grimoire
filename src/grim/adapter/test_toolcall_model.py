"""Tests for adapter/toolcall_model.py's _parse_actions — offline, using
fake litellm response objects (no API key, no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

from minisweagent.exceptions import FormatError  # noqa: E402

from grim.adapter.toolcall_model import GrimToolcallModel  # noqa: E402


def _tool_call(name: str, args: object, call_id: str = "call_1") -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def _response(
    tool_calls: list[SimpleNamespace], finish_reason: str = "tool_calls"
) -> SimpleNamespace:
    choice = SimpleNamespace(
        message=SimpleNamespace(tool_calls=tool_calls), finish_reason=finish_reason
    )
    return SimpleNamespace(choices=[choice])


def _model() -> GrimToolcallModel:
    # __init__ only builds config (no network) when no model registry is set.
    return GrimToolcallModel(model_name="deepseek/deepseek-v4-flash")


def test_valid_tool_call_lowers_to_a_grim_action() -> None:
    actions = _model()._parse_actions(_response([_tool_call("find", {"query": "github repos"})]))
    assert actions == [
        {
            "tool": "find",
            "args": {"query": "github repos"},
            "tool_call_id": "call_1",
            # every action carries a display command for mini's InteractiveAgent
            "command": "grim find 'github repos'",
        }
    ]


def test_parallel_tool_calls_all_parse() -> None:
    # Decision: parallel tool calls are allowed — each becomes its own action.
    resp = _response(
        [
            _tool_call("find", {"query": "x"}, "c1"),
            _tool_call("list", {"lang": "python"}, "c2"),
        ]
    )
    actions = _model()._parse_actions(resp)
    assert [a["tool"] for a in actions] == ["find", "list"]
    assert [a["tool_call_id"] for a in actions] == ["c1", "c2"]


def test_submit_is_a_valid_tool_call() -> None:
    actions = _model()._parse_actions(_response([_tool_call("submit", {"result": "118 repos"})]))
    assert actions == [
        {
            "tool": "submit",
            "args": {"result": "118 repos"},
            "tool_call_id": "call_1",
            "command": "submit",
        }
    ]


def test_no_tool_calls_raises_format_error() -> None:
    with pytest.raises(FormatError):
        _model()._parse_actions(_response([]))


def test_unknown_tool_falls_through_to_a_script_run() -> None:
    # Library fallthrough (the dominant real-world "format error" before it
    # became a feature): a model calling a script name directly, with an
    # invented schema, lowers to run(name=...) with scalar values as argv.
    actions = _model()._parse_actions(_response([_tool_call("read_file", {"path": "x.py"})]))
    assert len(actions) == 1
    assert actions[0]["tool"] == "run"
    assert actions[0]["args"] == {"name": "read_file", "args": ["x.py"]}


def test_fallthrough_scalars_become_argv_in_call_order() -> None:
    actions = _model()._parse_actions(
        _response([_tool_call("read_file", {"path": "x.py", "start": 2, "end": 5})])
    )
    assert actions[0]["args"] == {"name": "read_file", "args": ["x.py", "2", "5"]}


def test_fallthrough_passes_run_keys_through() -> None:
    actions = _model()._parse_actions(
        _response([_tool_call("apply_patch", {"stdin": "diff text", "args": ["-v"]})])
    )
    assert actions[0]["args"] == {"name": "apply_patch", "args": ["-v"], "stdin": "diff text"}


def test_fallthrough_with_no_arguments_is_a_bare_run() -> None:
    actions = _model()._parse_actions(_response([_tool_call("stats", {})]))
    assert actions[0]["args"] == {"name": "stats"}


def test_fallthrough_bool_lowers_as_shell_style_literal() -> None:
    actions = _model()._parse_actions(_response([_tool_call("list_bg", {"verbose": True})]))
    assert actions[0]["args"] == {"name": "list_bg", "args": ["true"]}


def test_fallthrough_rejects_non_scalar_arguments() -> None:
    # A nested object gives no honest argv ordering — precise FormatError,
    # pointing at the explicit run() form, never a guessed execution.
    with pytest.raises(FormatError) as exc:
        _model()._parse_actions(_response([_tool_call("edit_file", {"config": {"path": "x.py"}})]))
    content = exc.value.messages[0]["content"]
    assert "Unknown tool 'edit_file'" in content
    assert "run(name='edit_file'" in content


def test_missing_required_arg_raises_format_error() -> None:
    # write without body — the required-args check must catch it.
    with pytest.raises(FormatError):
        _model()._parse_actions(
            _response([_tool_call("write", {"name": "g", "lang": "python", "desc": "d"})])
        )


def test_malformed_json_arguments_raise_format_error() -> None:
    with pytest.raises(FormatError):
        _model()._parse_actions(_response([_tool_call("find", "{not valid json")]))


def test_list_where_string_expected_raises_format_error() -> None:
    # A list in a string-typed field must become a precise FormatError (the
    # model correction loop), not crash render_command later with
    # "expected string object, got 'list'".
    with pytest.raises(FormatError):
        _model()._parse_actions(_response([_tool_call("find", {"query": ["a", "b"]})]))


def test_type_error_message_names_the_offending_argument() -> None:
    with pytest.raises(FormatError) as exc:
        _model()._parse_actions(_response([_tool_call("find", {"query": ["a", "b"]})]))
    assert "'query'" in exc.value.messages[0]["content"]


def test_non_string_run_args_raise_format_error() -> None:
    # run's args schema is an array of strings; a nested list is invalid.
    with pytest.raises(FormatError):
        _model()._parse_actions(_response([_tool_call("run", {"name": "x", "args": ["a", ["b"]]})]))


def test_bool_is_rejected_for_numeric_fields() -> None:
    # bool subclasses int; the schema wants a real integer.
    with pytest.raises(FormatError):
        _model()._parse_actions(_response([_tool_call("read", {"exec": True})]))
