"""Runtime tests for the browser seeds in seeds/bodies_web.py (feature 6,
docs/features/06-browser.md), dispatched exactly as `grim run` would. These
are the calls web_open refuses before it touches tmux or Chrome; the tests
that drive a real Chrome sit beside them."""

from __future__ import annotations

from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_TIMEOUT_S = 120.0
_USAGE_EXIT_CODE = 2


def _run(name: str, argv: list[str]) -> ExecutionResult:
    seed = next(s for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language=seed.language, body=seed.body), request)


@pytest.mark.parametrize("argv", [[], ["ftp://example.com"], ["example.com"], ["a", "b"]])
def test_web_open_wrong_calls_exit_2(argv: list[str]) -> None:
    result = _run("web_open", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage: web_open URL" in result.stderr


def test_web_open_refuses_a_daily_chrome_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    daily = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    monkeypatch.setenv("GRIM_CHROME_DIR", str(daily))
    result = _run("web_open", ["http://localhost:8000"])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "that is a daily Chrome profile, not grim's own" in result.stderr
