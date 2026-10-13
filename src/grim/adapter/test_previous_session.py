"""Tests for adapter/previous_session.py — the --continue snippet. Moved
with the code from test_context.py; assertions unchanged."""

from __future__ import annotations

from pathlib import Path

from grim.adapter.previous_session import previous_session_snippet


def test_previous_session_snippet(tmp_path: Path) -> None:
    traj = tmp_path / "grimoire-test.traj.json"
    traj.write_text(
        '{"info": {"exit_status": "Submitted"}, "messages": ['
        '{"role": "user", "content": "task one"},'
        '{"role": "assistant", "content": "reasoning"},'
        '{"role": "tool", "content": "output"}]}'
    )
    pointer = tmp_path / "last-trajectory"
    pointer.write_text(str(traj) + "\n")
    snippet = previous_session_snippet(pointer)
    assert "Submitted" in snippet
    assert "reasoning" in snippet and "output" in snippet


def test_previous_session_snippet_degrades(tmp_path: Path) -> None:
    assert previous_session_snippet(tmp_path / "absent") == ""
    bad = tmp_path / "bad.traj.json"
    bad.write_text("not json")
    pointer = tmp_path / "p"
    pointer.write_text(str(bad) + "\n")
    assert previous_session_snippet(pointer) == ""
    other = tmp_path / "other"
    other.write_text("x")
    pointer.write_text(str(other) + "\n")
    assert previous_session_snippet(pointer) == ""
