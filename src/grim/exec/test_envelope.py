"""Tests for exec/envelope.py's head/tail truncation formatting."""

from __future__ import annotations

from grim.exec.envelope import truncate


def test_short_stdout_passes_through_unchanged() -> None:
    result = truncate("line1\nline2", "")
    assert result == "--- stdout: 2 lines ---\nline1\nline2"


def test_empty_stderr_produces_no_stderr_section() -> None:
    result = truncate("hi", "")
    assert "stderr" not in result


def test_long_stdout_truncated_to_head_and_tail() -> None:
    lines = [f"line{i}" for i in range(1, 61)]
    text = "\n".join(lines)
    result = truncate(text, "", head_lines=40, tail_lines=10)

    assert "--- stdout: first 40 + last 10 of 60 lines ---" in result
    assert "line1\n" in result
    assert "line40" in result
    assert "line51" in result
    assert "line60" in result
    assert "line41" not in result
    assert "... (10 skipped) ..." in result


def test_stderr_included_when_present() -> None:
    result = truncate("out", "boom")
    assert "--- stdout: 1 lines ---\nout" in result
    assert "--- stderr: 1 lines ---\nboom" in result
