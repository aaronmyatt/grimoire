"""Tests for exec/gobuild.py (docs/features/05-go-build.md): Go scripts build
once against a shared module and then run from a cache. The "third-party"
package is a local stand-in wired in with a `replace` directive, so nothing
is downloaded. Ref: https://go.dev/ref/mod#go-mod-file-replace"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from grim.exec import gobuild
from grim.exec.dispatch import ExecutionRequest, ScriptVersion, dispatch

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="builds real Go programs")

_TIMEOUT_S = 120.0
_GO_MOD = """module grimscripts

go 1.21

require example.com/standin v0.0.0

replace example.com/standin => ./standin
"""
_STANDIN = 'package standin\n\nfunc Greeting() string { return "hello from the module" }\n'
_SCRIPT = """package main

import (
	"fmt"
	"os"

	"example.com/standin"
)

func main() {
	cwd, _ := os.Getwd()
	fmt.Println(standin.Greeting(), "MARK", os.Args[1:], cwd)
}
"""


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "go"
    (root / "standin").mkdir(parents=True)
    (root / "go.mod").write_text(_GO_MOD)
    (root / "standin" / "go.mod").write_text("module example.com/standin\n\ngo 1.21\n")
    (root / "standin" / "standin.go").write_text(_STANDIN)
    monkeypatch.setenv("GRIM_GO_DIR", str(root))
    monkeypatch.setenv("GOFLAGS", "-mod=mod")
    monkeypatch.setenv("GOPROXY", "off")  # prove nothing is fetched
    (tmp_path / "work").mkdir()
    return root


def _go(body: str, tmp_path: Path, argv: list[str] | None = None) -> tuple[int, str, str]:
    request = ExecutionRequest(
        argv=argv or [], stdin=None, cwd=str(tmp_path / "work"), timeout=_TIMEOUT_S
    )
    result = dispatch(ScriptVersion(language="go", body=body), request)
    return result.exit_code, result.stdout, result.stderr


@needs_go
def test_first_run_builds_against_the_shared_module_and_runs_in_the_scripts_cwd(
    module: Path, tmp_path: Path
) -> None:
    exit_code, stdout, stderr = _go(_SCRIPT, tmp_path, ["a", "b c"])
    assert exit_code == 0, stderr
    work = (tmp_path / "work").resolve()
    assert stdout == f"hello from the module MARK [a b c] {work}\n"
    assert [path.name for path in (module / "bin").iterdir()] == [
        gobuild.cache_key(_SCRIPT, module)
    ]


@needs_go
def test_second_run_uses_the_cached_binary_with_no_compile(
    module: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _go(_SCRIPT, tmp_path)[0] == 0

    def no_compile(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a cached script must not be compiled again")

    monkeypatch.setattr(gobuild, "_compile", no_compile)
    exit_code, stdout, _ = _go(_SCRIPT, tmp_path)
    assert exit_code == 0
    assert stdout.startswith("hello from the module MARK")


@needs_go
def test_a_new_version_builds_a_new_binary_and_leaves_the_old_one_unused(
    module: Path, tmp_path: Path
) -> None:
    updated = _SCRIPT.replace("MARK", "V2")
    assert _go(_SCRIPT, tmp_path)[1].count("MARK") == 1
    assert _go(updated, tmp_path)[1].count("V2") == 1
    names = {path.name for path in (module / "bin").iterdir()}
    assert names == {gobuild.cache_key(_SCRIPT, module), gobuild.cache_key(updated, module)}


@needs_go
def test_a_build_error_is_the_scripts_own_failed_run(module: Path, tmp_path: Path) -> None:
    exit_code, stdout, stderr = _go(_SCRIPT.replace("standin.Greeting()", "nope()"), tmp_path)
    assert exit_code != 0
    assert stdout == ""
    assert "undefined: nope" in stderr  # the compiler's own message
    assert list((module / "bin").iterdir()) == []  # nothing half-built is kept


@needs_go
def test_the_cache_removes_the_least_recently_used_binary_at_its_cap(
    module: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gobuild, "MAX_BINARIES", 1)
    updated = _SCRIPT.replace("MARK", "V2")
    assert _go(_SCRIPT, tmp_path)[0] == 0
    assert _go(updated, tmp_path)[0] == 0
    assert [path.name for path in (module / "bin").iterdir()] == [
        gobuild.cache_key(updated, module)
    ]


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
