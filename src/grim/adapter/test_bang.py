"""Tests for adapter/bang.py — `!slug` execute-and-substitute."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from grim.adapter.bang import MAX_BANGS_PER_MESSAGE, expand_bangs, install_bang_expansion


def _runner(calls: list[str]) -> Callable[[str], str]:
    def run(slug: str) -> str:
        calls.append(slug)
        return f"<output of {slug}>"

    return run


def test_no_bang_returns_text_unchanged() -> None:
    calls: list[str] = []
    text = "just a normal sentence, nothing to see here"

    assert expand_bangs(text, _runner(calls)) == text
    assert calls == []


def test_single_bang_is_replaced() -> None:
    calls: list[str] = []

    result = expand_bangs("check !check_disk please", _runner(calls))

    assert result == "check <output of check_disk> please"
    assert calls == ["check_disk"]


def test_punctuation_is_not_a_bang() -> None:
    calls: list[str] = []
    text = "wow! that worked, e.g.!foo_bar was close"

    assert expand_bangs(text, _runner(calls)) == text
    assert calls == []


def test_multiple_bangs_each_replaced() -> None:
    calls: list[str] = []

    result = expand_bangs("!alpha then !beta", _runner(calls))

    assert result == "<output of alpha> then <output of beta>"
    assert calls == ["alpha", "beta"]


def test_bangs_beyond_the_cap_are_left_literal() -> None:
    calls: list[str] = []
    slugs = [f"slug{i:02d}" for i in range(MAX_BANGS_PER_MESSAGE + 1)]
    text = " ".join(f"!{s}" for s in slugs)

    result = expand_bangs(text, _runner(calls))

    assert len(calls) == MAX_BANGS_PER_MESSAGE
    assert f"!{slugs[-1]}" in result  # last one, past the cap, untouched
    assert f"<output of {slugs[-1]}>" not in result


def test_install_wraps_both_sessions_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("minisweagent")
    from minisweagent.agents.utils import prompt_user

    install_bang_expansion("session-1")
    first_prompt_wrapper = prompt_user.prompt_session.prompt
    first_multiline_wrapper = prompt_user._multiline_prompt_session.prompt

    install_bang_expansion("session-1")  # repeat call must not double-wrap

    assert prompt_user.prompt_session.prompt is first_prompt_wrapper
    assert prompt_user._multiline_prompt_session.prompt is first_multiline_wrapper
