"""Build-once cache for Go scripts (docs/features/05-go-build.md).

`go run FILE` cannot see a third-party package: a lone file has no module. So
when a shared module exists ($GRIM_GO_DIR, else ~/.grimoire/go, with a go.mod
that pins the packages scripts may import), a Go script is built against it
once — `go build -C MODULE -o BIN FILE` — and the binary is kept under
MODULE/bin, named by a hash of the script body and the module's go.mod/go.sum.
The same body then starts with no compile. With no shared module, dispatch
keeps using plain `go run`.

The cache is derived data: content-addressed, capped, safe to delete.
Ref: https://pkg.go.dev/cmd/go#hdr-Compile_packages_and_dependencies
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

MODULE_ENV = "GRIM_GO_DIR"
MAX_BINARIES = 64  # least recently used ones beyond this are removed
BUILD_TIMEOUT_EXIT_CODE = 124  # same code a timed-out run gets (dispatch.TIMEOUT_EXIT_CODE)
GO_MISSING_EXIT_CODE = 127
_KEY_HEX_CHARS = 32
_MODULE_FILES = ("go.mod", "go.sum")


class Built(NamedTuple):
    """Outcome of build_cached: the binary to run, or why there is none."""

    binary: Path | None
    exit_code: int
    stderr: str
    duration_ms: int


def module_dir() -> Path | None:
    """The shared Go module scripts build against, or None when there is none."""
    root = Path(os.environ.get(MODULE_ENV) or Path.home() / ".grimoire" / "go")
    found = root if (root / "go.mod").is_file() else None
    assert found is None or found.is_dir(), "a module with a go.mod is a directory"
    assert found is None or found == root, "the module is the configured root or nothing"
    return found


def cache_key(body: str, module: Path) -> str:
    """Names a binary by what it is built from: the script body and the
    module's go.mod and go.sum. Change any of them and the name changes."""
    assert (module / "go.mod").is_file(), f"no go.mod in {module}"
    digest = hashlib.sha256()
    parts = [body.encode()]
    parts += [(module / name).read_bytes() for name in _MODULE_FILES if (module / name).is_file()]
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))  # length-prefixed: no ambiguous joins
        digest.update(part)
    key = digest.hexdigest()[:_KEY_HEX_CHARS]
    assert len(key) == _KEY_HEX_CHARS, "a cache key has a fixed length"
    return key


def evict(bin_dir: Path, keep: int) -> list[Path]:
    """Remove the least recently used binaries beyond `keep` (by mtime, which
    build_cached refreshes on every use). Returns what was removed."""
    assert keep >= 1, "the cache always keeps at least one binary"
    binaries = [path for path in bin_dir.iterdir() if path.is_file() and path.name[0] != "."]
    binaries.sort(key=lambda path: path.stat().st_mtime_ns)
    doomed = binaries[: max(0, len(binaries) - keep)]
    for path in doomed:
        path.unlink(missing_ok=True)
    assert len(binaries) - len(doomed) <= keep, "the cap holds after eviction"
    return doomed


def _compile(module: Path, source: Path, staged: Path, timeout_s: float) -> tuple[int, str]:
    """Run `go build`; returns (exit code, compiler output)."""
    assert timeout_s > 0, "a build always has a positive timeout"
    command = ["go", "build", "-C", str(module), "-o", str(staged), str(source)]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BUILD_TIMEOUT_EXIT_CODE, f"go build timed out after {timeout_s:g}s"
    except FileNotFoundError:
        return GO_MISSING_EXIT_CODE, "go build: the `go` program is not on PATH"
    assert isinstance(done.returncode, int), "a finished build has an exit code"
    return done.returncode, done.stderr


def build_cached(body: str, timeout_s: float) -> Built | None:
    """The cached binary for `body`, building it first if needed. None means
    there is no shared module (the caller falls back to `go run`); a Built
    with no binary is a failed build — its exit code and compiler output are
    the script's own failure."""
    assert timeout_s > 0, "a build always has a positive timeout"
    module = module_dir()
    if module is None:
        return None
    bin_dir = module / "bin"
    bin_dir.mkdir(exist_ok=True)
    binary = bin_dir / cache_key(body, module)
    if binary.is_file():
        os.utime(binary)  # mark it used: eviction is least-recently-used
        return Built(binary, 0, "", 0)
    started = time.monotonic()
    staged = bin_dir / f".{binary.name}.{os.getpid()}"  # built aside, then renamed: atomic
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "main.go"
        source.write_text(body)
        exit_code, output = _compile(module, source, staged, timeout_s)
    duration_ms = int((time.monotonic() - started) * 1000)
    if exit_code != 0:
        staged.unlink(missing_ok=True)
        return Built(None, exit_code, output, duration_ms)
    os.replace(staged, binary)
    evict(bin_dir, MAX_BINARIES)
    assert binary.is_file(), "a successful build leaves its binary in the cache"
    return Built(binary, 0, output, duration_ms)
