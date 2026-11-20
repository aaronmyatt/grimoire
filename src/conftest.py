"""Shared pytest fixtures for the grim test suite.

Autouse: `grim init` installs shell completion (src/grim/completions.py), so
redirect GRIM_COMPLETIONS_DIR / GRIM_BASHRC / GRIM_ZSHRC to per-test temp dirs
and keep the suite from ever touching real dotfiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _redirect_completions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_COMPLETIONS_DIR", str(tmp_path / "completions"))
    monkeypatch.setenv("GRIM_BASHRC", str(tmp_path / ".bashrc"))
    monkeypatch.setenv("GRIM_ZSHRC", str(tmp_path / ".zshrc"))


# Vars a live agent session exports into every child process (grim run agent
# itself sets them). They must not leak into tests that assert on the scoped
# export or on session metadata, so the suite deletes them per test.
_SESSION_ENV = (
    "GRIM_SESSION",
    "GRIM_SESSION_TASK",
    "GRIM_SESSION_MODEL",
    "GRIM_MODEL",
    "GRIM_CWD",
    "GRIM_CALL_DEPTH",
)


@pytest.fixture(autouse=True)
def _no_ambient_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic suite: drop any agent-session env inherited from the caller."""
    for name in _SESSION_ENV:
        monkeypatch.delenv(name, raising=False)
