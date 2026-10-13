"""Runtime tests for the no-shell program seeds in seeds/bodies_exec.py
(run_cmd, run_py), dispatched exactly as `grim run` would. run_bg's tests
stay in test_bodies.py beside list_bg/stop_bg."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_TIMEOUT_S = 10.0
_USAGE_EXIT_CODE = 2
_NOT_FOUND_EXIT_CODE = 127
_FALSE_EXIT_CODE = 1


def _run(name: str, argv: list[str], stdin: str | None = None) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=stdin, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def test_run_cmd_runs_one_program_with_args() -> None:
    result = _run("run_cmd", ["echo", "hello world"])
    assert result.exit_code == 0
    assert result.stdout == "hello world\n"


def test_run_cmd_splits_a_single_command_string() -> None:
    result = _run("run_cmd", ["echo one two"])
    assert result.stdout == "one two\n"


def test_run_cmd_passes_exit_code_and_stdin_through() -> None:
    assert _run("run_cmd", ["false"]).exit_code == _FALSE_EXIT_CODE
    assert _run("run_cmd", [sys.executable, "-c", "print(input())"], stdin="piped\n").stdout == (
        "piped\n"
    )


@pytest.mark.parametrize(
    "argv",
    [["echo hi | cat"], ["echo", "hi", "&&", "ls"], ["bash", "-c", "echo hi"], ["/bin/sh x"]],
    ids=["pipe", "and-chain", "bash", "sh-path"],
)
def test_run_cmd_refuses_shell_syntax_and_shells(argv: list[str]) -> None:
    result = _run("run_cmd", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "no shell here" in result.stderr


def test_run_cmd_hints_at_a_better_seed_but_still_runs(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("content\n")
    result = _run("run_cmd", ["cat", str(target)])
    assert result.exit_code == 0
    assert result.stdout == "content\n"
    assert "read_file" in result.stderr


def test_run_cmd_points_a_library_script_name_at_run() -> None:
    result = _run("run_cmd", ["list_open_ports_xyz"])
    assert result.exit_code == _NOT_FOUND_EXIT_CODE
    assert 'run(name="list_open_ports_xyz")' in result.stderr
