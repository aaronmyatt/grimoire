"""Smoke test for `grim init` end to end, via GRIM_DB (never the real db)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grim import cli


def test_grim_init_creates_db_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "grimoire.db"
    monkeypatch.setenv("GRIM_DB", str(db_path))

    exit_code = cli.main(["init"])

    assert exit_code == 0
    assert db_path.exists()


def test_grim_init_twice_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))

    first = cli.main(["init"])
    second = cli.main(["init"])

    assert first == 0
    assert second == 0


def test_verb_before_init_warns_and_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))

    exit_code = cli.main(["list"])

    assert exit_code == 1
    assert "grim init" in capsys.readouterr().err


def test_run_parser_accepts_stdin_file_after_the_name() -> None:
    # Regression: `args` used to be nargs=REMAINDER, which swallowed every
    # token after NAME including flags like --stdin-file — the documented
    # `grim run NAME [--stdin-file F]` syntax (build plan §4) didn't work.
    args = cli.build_parser().parse_args(["run", "greet", "--stdin-file", "f.txt"])
    assert args.name == "greet"
    assert args.stdin_file == "f.txt"
    assert args.args == []


def test_run_parser_accepts_stdin_file_before_the_name() -> None:
    args = cli.build_parser().parse_args(["run", "--stdin-file", "f.txt", "greet"])
    assert args.name == "greet"
    assert args.stdin_file == "f.txt"


def test_run_parser_strips_the_double_dash_separator_from_trailing_args() -> None:
    args = cli.build_parser().parse_args(["run", "greet", "--", "echo", "hi"])
    assert args.args == ["echo", "hi"]
