"""Tests for `grim completion` (src/grim/completions.py) — install/uninstall/
check, always redirected to temp paths via GRIM_COMPLETIONS_DIR / GRIM_BASHRC /
GRIM_ZSHRC so real dotfiles are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grim import cli, completions


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "completions"
    monkeypatch.setenv("GRIM_COMPLETIONS_DIR", str(d))
    monkeypatch.setenv("GRIM_BASHRC", str(tmp_path / ".bashrc"))
    monkeypatch.setenv("GRIM_ZSHRC", str(tmp_path / ".zshrc"))
    return d


def test_install_writes_files_and_hooks(
    isolated: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["completion"]) == 0
    assert (isolated / "grim.bash").is_file()
    assert (isolated / "_grim").is_file()
    assert "grim.bash" in (tmp_path / ".bashrc").read_text()
    assert "completions" in (tmp_path / ".zshrc").read_text()
    assert "wrote" in capsys.readouterr().out


def test_install_is_idempotent(isolated: Path, tmp_path: Path) -> None:
    assert cli.main(["completion"]) == 0
    bashrc_before = (tmp_path / ".bashrc").read_text()
    assert cli.main(["completion"]) == 0
    assert (tmp_path / ".bashrc").read_text() == bashrc_before


def test_uninstall_removes_files_and_hooks(isolated: Path, tmp_path: Path) -> None:
    assert cli.main(["completion"]) == 0
    assert cli.main(["completion", "--uninstall"]) == 0
    assert not (isolated / "grim.bash").exists()
    assert not (isolated / "_grim").exists()
    assert "grim.bash" not in (tmp_path / ".bashrc").read_text()
    assert "completions" not in (tmp_path / ".zshrc").read_text()


def test_check_reports_installed(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["completion"]) == 0
    assert cli.main(["completion", "--check"]) == 0
    out = capsys.readouterr().out
    assert "ok:" in out
    assert "missing" not in out


def test_check_reports_missing(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["completion", "--check"]) == 1
    assert "missing" in capsys.readouterr().out


def test_snippets_query_the_script_table() -> None:
    assert "SELECT name FROM script" in completions.BASH_COMPLETION
    assert "SELECT name FROM script" in completions.ZSH_COMPLETION


def test_selftest_passes() -> None:
    assert cli.main(["completion", "--selftest"]) == 0
