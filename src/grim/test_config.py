"""Tests for grim/config.py — global config file seeding env-var defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grim import config


def test_apply_seeds_env_defaults_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "prov/m"\ntimeout = 300\n')
    # delenv (not just absence) so monkeypatch restores/removes these on
    # teardown — apply_global_config writes os.environ directly.
    monkeypatch.delenv("GRIM_MODEL", raising=False)
    monkeypatch.delenv("GRIM_TIMEOUT", raising=False)

    config.apply_global_config(cfg)

    expected_timeout = "300"  # int in TOML -> str for the env var
    assert os.environ["GRIM_MODEL"] == "prov/m"
    assert os.environ["GRIM_TIMEOUT"] == expected_timeout


def test_shell_env_wins_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "from/file"\n')
    monkeypatch.setenv("GRIM_MODEL", "from/shell")

    config.apply_global_config(cfg)

    assert os.environ["GRIM_MODEL"] == "from/shell"  # setdefault never overwrites


def test_missing_file_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIM_MODEL", raising=False)

    config.apply_global_config(tmp_path / "does-not-exist.toml")

    assert "GRIM_MODEL" not in os.environ


def test_malformed_toml_warns_and_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("model = = broken")  # invalid TOML
    monkeypatch.delenv("GRIM_MODEL", raising=False)

    config.apply_global_config(cfg)  # must not raise

    assert "GRIM_MODEL" not in os.environ
    assert "ignoring" in capsys.readouterr().err


def test_unknown_keys_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('nonsense_key = "x"\n')

    config.apply_global_config(cfg)  # no mapping -> nothing seeded, no crash

    assert "NONSENSE_KEY" not in os.environ


def test_new_keys_seed_run_dir_cost_and_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('run_dir = "/jobs"\ncost = 5\nstep = 10\n')
    for var in ("GRIM_RUN_DIR", "GRIM_COST_LIMIT", "GRIM_STEP_LIMIT"):
        monkeypatch.delenv(var, raising=False)

    config.apply_global_config(cfg)

    assert os.environ["GRIM_RUN_DIR"] == "/jobs"
    assert os.environ["GRIM_COST_LIMIT"] == "5"
    assert os.environ["GRIM_STEP_LIMIT"] == "10"


def _make_repo(tmp_path: Path, model: str) -> Path:
    repo = tmp_path / "proj"
    (repo / ".grimoire").mkdir(parents=True)
    (repo / ".grimoire" / "config.toml").write_text(f'model = "{model}"\n')
    return repo


def test_repo_config_overrides_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path, "repo/m")
    global_cfg = tmp_path / "global.toml"
    global_cfg.write_text('model = "global/m"\ntimeout = 300\n')
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GRIM_MODEL", raising=False)
    monkeypatch.delenv("GRIM_TIMEOUT", raising=False)

    config.apply_global_config(global_cfg)

    assert os.environ["GRIM_MODEL"] == "repo/m"  # repo wins over global
    assert os.environ["GRIM_TIMEOUT"] == "300"  # global fills what repo omits


def test_shell_env_beats_repo_and_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path, "repo/m")
    global_cfg = tmp_path / "global.toml"
    global_cfg.write_text('model = "global/m"\n')
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GRIM_MODEL", "shell/m")

    config.apply_global_config(global_cfg)

    assert os.environ["GRIM_MODEL"] == "shell/m"  # env beats both files
