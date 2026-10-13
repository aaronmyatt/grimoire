"""Tests for adapter/trajectories.py — trajectory location and the keep cap.
Pure filesystem work against tmp dirs; no model, no mini import."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grim.adapter import trajectories

_KEEP = 2
_OLDEST_MTIME = 1_000_000


def _touch(directory: Path, name: str, mtime: int) -> Path:
    path = directory / name
    path.write_text("{}")
    os.utime(path, (mtime, mtime))
    return path


def test_default_dir_lives_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(trajectories.TRAJ_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert trajectories.trajectory_dir() == tmp_path / ".grimoire" / "trajectories"


def test_env_dir_wins_and_is_made_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(trajectories.TRAJ_DIR_ENV, "runs")
    assert trajectories.trajectory_dir() == tmp_path / "runs"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", trajectories.DEFAULT_KEEP), ("abc", trajectories.DEFAULT_KEEP), ("0", 1), ("7", 7)],
)
def test_keep_limit_parses_and_clamps(
    raw: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(trajectories.TRAJ_KEEP_ENV, raw)
    assert trajectories.keep_limit() == expected


def test_prune_deletes_only_the_oldest_grim_files(tmp_path: Path) -> None:
    old = _touch(tmp_path, "grimoire-a-1.traj.json", _OLDEST_MTIME)
    mid = _touch(tmp_path, "grimoire-mini-b-2.traj.json", _OLDEST_MTIME + 10)
    new = _touch(tmp_path, "grimoire-c-3.traj.json", _OLDEST_MTIME + 20)
    foreign = _touch(tmp_path, "other-tool.traj.json", 0)

    assert trajectories.prune(tmp_path, _KEEP) == 1
    assert not old.exists()
    assert mid.exists() and new.exists() and foreign.exists()


def test_prune_under_the_limit_deletes_nothing(tmp_path: Path) -> None:
    _touch(tmp_path, "grimoire-a-1.traj.json", _OLDEST_MTIME)
    assert trajectories.prune(tmp_path, _KEEP) == 0


def test_next_path_creates_the_dir_and_prunes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "traj"
    target.mkdir()
    old = _touch(target, "grimoire-a-1.traj.json", _OLDEST_MTIME)
    _touch(target, "grimoire-b-2.traj.json", _OLDEST_MTIME + 10)
    monkeypatch.setenv(trajectories.TRAJ_DIR_ENV, str(target / "nested"))
    monkeypatch.setenv(trajectories.TRAJ_KEEP_ENV, "1")

    path = trajectories.next_path("20261013T120000", 42)
    assert path == str(target / "nested" / "grimoire-20261013T120000-42.traj.json")
    assert (target / "nested").is_dir()
    assert old.exists()  # pruning only touches the configured dir

    monkeypatch.setenv(trajectories.TRAJ_DIR_ENV, str(target))
    trajectories.next_path("20261013T120001", 42)
    assert not old.exists()
