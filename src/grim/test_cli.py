"""Smoke test for `grim init` end to end, via GRIM_DB (never the real db)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from grim import cli, config


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


def test_grim_config_reports_value_and_global_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    global_cfg = tmp_path / "config.toml"
    global_cfg.write_text('model = "cfg/model"\n')
    monkeypatch.setattr(config, "CONFIG_PATH", global_cfg)
    monkeypatch.chdir(tmp_path)  # no ./.grimoire here, so no repo layer
    monkeypatch.delenv("GRIM_MODEL", raising=False)
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))

    exit_code = cli.main(["config"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GRIM_MODEL" in out
    assert "cfg/model" in out
    assert "global" in out


def test_grim_config_marks_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    global_cfg = tmp_path / "config.toml"
    global_cfg.write_text('model = "cfg/model"\n')
    monkeypatch.setattr(config, "CONFIG_PATH", global_cfg)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GRIM_MODEL", "shell/model")
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))

    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "shell/model" in out  # shell value wins, classified as env


def test_grim_config_works_without_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # config is a diagnostic — it must not be gated behind `grim init`.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "never-initialized.db"))

    assert cli.main(["config"]) == 0
    assert "GRIM_MODEL" in capsys.readouterr().out


def test_grim_doctor_reports_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    cli.main(["init"])  # so the database check reads as migrated
    capsys.readouterr()

    exit_code = cli.main(["doctor"])

    out = capsys.readouterr().out
    assert exit_code == 0  # required tools + fts5 present in the test env
    for token in ("uv", "bash", "fts5", "database"):
        assert token in out


def test_grim_doctor_works_without_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "never.db"))

    exit_code = cli.main(["doctor"])  # unmigrated DB is a warn, not a hard fail

    assert exit_code == 0
    assert "database" in capsys.readouterr().out


def test_grim_doctor_fails_when_required_tool_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    real_which = shutil.which
    # hide `uv` (a required tool) but leave everything else discoverable.
    # Patch the shutil module cli imports, not cli.shutil (not re-exported).
    monkeypatch.setattr(shutil, "which", lambda t: None if t == "uv" else real_which(t))

    exit_code = cli.main(["doctor"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out
