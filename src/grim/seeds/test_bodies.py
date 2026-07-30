"""Tests for seeds/bodies.py — each seed's actual runtime behavior,
dispatched exactly the way `grim run` would (exec.dispatch), not just
"does it look like valid python".
"""

from __future__ import annotations

from pathlib import Path

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_TIMEOUT_S = 10.0


def _seed_body(name: str) -> str:
    return next(s.body for s in SEEDS if s.name == name)


def _run(
    name: str, argv: list[str] | None = None, stdin: str | None = None, cwd: str | None = None
) -> ExecutionResult:
    script_version = ScriptVersion(language="python", body=_seed_body(name))
    request = ExecutionRequest(argv=argv or [], stdin=stdin, cwd=cwd, timeout=_TIMEOUT_S)
    return dispatch(script_version, request)


def test_shell_runs_a_shell_command() -> None:
    result = _run("shell", argv=["echo hello"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


def test_shell_propagates_exit_code() -> None:
    expected_exit_code = 3
    result = _run("shell", argv=[f"exit {expected_exit_code}"])
    assert result.exit_code == expected_exit_code


def test_read_file_prints_whole_file(tmp_path: Path) -> None:
    target = tmp_path / "greeting.txt"
    target.write_text("line1\nline2\nline3\n")

    result = _run("read_file", argv=[str(target)])

    assert result.exit_code == 0
    assert result.stdout == "1\tline1\n2\tline2\n3\tline3\n"


def test_read_file_respects_line_range(tmp_path: Path) -> None:
    target = tmp_path / "greeting.txt"
    target.write_text("line1\nline2\nline3\n")

    result = _run("read_file", argv=[str(target), "2", "2"])

    assert result.stdout == "2\tline2\n"


def test_write_file_writes_stdin_to_path(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    content = "hello world"

    result = _run("write_file", argv=[str(target)], stdin=content)

    assert result.exit_code == 0
    assert target.read_text() == content
    assert f"wrote {len(content)} bytes" in result.stdout
