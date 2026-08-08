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
