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


def test_none_limits_emit_full_output() -> None:
    # None/None is how `grim run` now requests untruncated output: every
    # line present, no "skipped" marker, even far past the old 40/10 window.
    lines = [f"line{i}" for i in range(1, 101)]
    result = truncate("\n".join(lines), "", head_lines=None, tail_lines=None)

    assert "--- stdout: 100 lines ---" in result
    assert "skipped" not in result
    assert "line1\n" in result
    assert "line50" in result
    assert "line100" in result


def test_head_only_limit_shows_top_and_no_tail() -> None:
    lines = [f"line{i}" for i in range(1, 61)]
    result = truncate("\n".join(lines), "", head_lines=5, tail_lines=None)

    assert "--- stdout: first 5 + last 0 of 60 lines ---" in result
    assert "line5" in result
    assert "line6" not in result
    assert "line60" not in result  # tail=0 must not accidentally show the end
    assert "... (55 skipped) ..." in result


def test_tail_only_limit_shows_bottom_and_no_head() -> None:
    lines = [f"line{i}" for i in range(1, 61)]
    result = truncate("\n".join(lines), "", head_lines=None, tail_lines=5)

    assert "--- stdout: first 0 + last 5 of 60 lines ---" in result
    assert "line56" in result
    assert "line60" in result
    assert "line1\n" not in result  # head=0 must not show the top
    assert "... (55 skipped) ..." in result
