"""run_dir_prune (seeds/bodies_prune.py, delegation-plan 5e): only old job logs and job
dirs whose tmux window is gone go, and only with --yes. Each test has its own run dir
and private tmux server; the real run dir is never read."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="it asks tmux for jobs")
_DAY_S, _MB = 86400, 1024 * 1024
# Too young, still has a window, not a log, a lock file: (name, days old) prune keeps.
_KEPT = (("young.log", 1), ("live.log", 30), ("notes.txt", 30), (".tmux-start-x.lock", 30))


def _prune(*argv: str) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == "run_dir_prune")
    request = ExecutionRequest(argv=list(argv), stdin=None, cwd=None, timeout=20.0)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _aged(path: Path, days: float, size_bytes: int = 10) -> Path:
    """A job log (NAME.log, any other file) or a job dir, last touched `days` ago."""
    target = path if path.suffix else path / "result.md"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"x" * size_bytes)
    then = time.time() - days * _DAY_S
    for touched in {target, path}:
        os.utime(touched, (then, then))  # Ref: https://docs.python.org/3/library/os.html#os.utime
    return path


@pytest.fixture
def run_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    socket = f"grimprune-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", socket)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    tmux = ["tmux", "-L", socket]  # job "live" still has its window, so it is never pruned
    subprocess.run(
        [*tmux, "new-session", "-d", "-s", "grim", "-n", "live", "sleep", "600"], check=True
    )
    subprocess.run([*tmux, "set-option", "-w", "-t", "=grim:live", "@grim_job", "live"], check=True)
    yield tmp_path / "run"
    subprocess.run([*tmux, "kill-server"], capture_output=True, check=False)
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / socket).unlink(missing_ok=True)


def _fill(run_dir: Path) -> tuple[list[Path], list[Path]]:
    """(the entries prune may delete, the ones it must keep), oldest first."""
    gone = [_aged(run_dir / "old.log", 30), _aged(run_dir / "old-claude", 20)]
    kept = [_aged(run_dir / name, days) for name, days in _KEPT]
    return gone, kept


def test_without_yes_it_lists_what_it_would_delete_and_deletes_nothing(run_dir: Path) -> None:
    gone, kept = _fill(run_dir)
    result = _prune("--keep-days", "7")
    assert result.exit_code == 0, result.stderr
    listed = [line.split("  ")[1] for line in result.stdout.splitlines()[:-1]]
    assert listed == [str(path) for path in gone]
    assert "would delete (run again with --yes) 2 entries" in result.stdout
    assert all(path.exists() for path in gone + kept)


def test_with_yes_it_deletes_only_old_logs_and_dirs_whose_window_is_gone(run_dir: Path) -> None:
    gone, kept = _fill(run_dir)
    result = _prune("--yes")
    assert result.exit_code == 0, result.stderr
    assert not any(path.exists() for path in gone)
    assert all(path.exists() for path in kept)


def test_max_mb_stops_once_the_run_dir_is_small_enough(run_dir: Path) -> None:
    oldest, newer = _aged(run_dir / "a.log", 30, _MB), _aged(run_dir / "b.log", 20, _MB)
    result = _prune("--max-mb", "1.5", "--yes")
    assert result.exit_code == 0, result.stderr
    assert not oldest.exists() and newer.exists()
