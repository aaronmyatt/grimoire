"""Tests for exec/gobuild.py (docs/features/05-go-build.md): the cache's own
rules, with no Go toolchain needed. The real-build tests sit beside these."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from grim.exec import gobuild

_TIMEOUT_S = 120.0


def test_evict_removes_oldest_used_first_and_keeps_the_cap(tmp_path: Path) -> None:
    for age, name in enumerate(["newest", "middle", "oldest"]):
        path = tmp_path / name
        path.write_text("x")
        os.utime(path, (1_000_000 - age, 1_000_000 - age))
    (tmp_path / ".staged.123").write_text("x")  # a build in flight is never evicted
    removed = gobuild.evict(tmp_path, keep=2)
    assert [path.name for path in removed] == ["oldest"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [".staged.123", "middle", "newest"]


def test_cache_key_changes_with_the_body_and_with_the_module(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module a\n")
    first = gobuild.cache_key("package main", tmp_path)
    assert first == gobuild.cache_key("package main", tmp_path)
    assert first != gobuild.cache_key("package main // v2", tmp_path)
    (tmp_path / "go.sum").write_text("example.com/x v1.0.0 h1:abc=\n")
    assert first != gobuild.cache_key("package main", tmp_path)


def test_no_shared_module_means_no_cache_and_plain_go_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_GO_DIR", str(tmp_path / "absent"))
    assert gobuild.module_dir() is None
    assert gobuild.build_cached("package main", _TIMEOUT_S) is None


def test_a_missing_go_program_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "go.mod").write_text("module a\n")
    monkeypatch.setenv("GRIM_GO_DIR", str(tmp_path))

    def missing(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("go")

    monkeypatch.setattr(subprocess, "run", missing)  # gobuild calls subprocess.run
    built = gobuild.build_cached("package main", _TIMEOUT_S)
    assert built is not None and built.binary is None
    assert built.exit_code == gobuild.GO_MISSING_EXIT_CODE
