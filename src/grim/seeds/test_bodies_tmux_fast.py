"""Phase 5 tmux seed tests (docs/delegation-plan.md), apart from test_bodies_tmux.py so
both stay under 500 lines. Each test has a private tmux server and run dir."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 20.0
_BIG_LOG_BYTES = 50 * 1024 * 1024
_FAST_READ_MS = 100  # delegation-plan R3: a tmux move under 100 ms
_SLEEPER = ["sleep", "600"]  # outlives the test; the fixture's kill-server ends it
_PARALLEL = 4
# Prints 5000 lines, then holds. Ref: https://man.openbsd.org/tmux#wait-for
_SCREEN = "subprocess.run(['tmux', 'capture-pane', '-p'], capture_output=True, text=True).stdout"
_PRINT_5000 = (
    f"import subprocess\nfor i in range(5000):\n    print('line', i)\n"
    f"while 'line 4999' not in {_SCREEN}:\n    pass\n"
    "subprocess.run(['tmux', 'wait-for', '-S', 'printed'])\n"
    "subprocess.run(['tmux', 'wait-for', 'hold'])\n"
)


def _run(name: str, argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    name = f"grimfast-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", name)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    yield name  # a copy of test_bodies_tmux.py's fixture (Ref: https://man.openbsd.org/tmux#L)
    _tmux(name, "kill-server")
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


def test_tmux_read_of_a_50mb_dead_log_is_fast_and_gives_the_last_lines(
    socket: str, tmp_path: Path
) -> None:
    assert _run("tmux_start", ["big", "true"]).exit_code == 0
    assert _tmux(socket, "wait-for", "grim-big").returncode == 0  # dead: its log is the record
    line_count = _BIG_LOG_BYTES // len("line 0000000\n")
    (tmp_path / "run" / "big.log").write_text("".join(f"line {i:07}\n" for i in range(line_count)))
    result = _run("tmux_read", ["big", "3"])
    assert result.exit_code == 0, result.stderr
    status, *lines = result.stdout.splitlines()
    assert lines == [f"line {i:07}" for i in range(line_count - 3, line_count)]
    assert "big: dead (exit 0)" in status and " · truncated" in status
    assert result.duration_ms < _FAST_READ_MS, f"a 50 MB log read took {result.duration_ms} ms"


def test_tmux_read_of_a_live_pane_reads_only_the_lines_asked_for(socket: str) -> None:
    assert _run("tmux_start", ["api", sys.executable, "-c", _PRINT_5000]).exit_code == 0
    assert _tmux(socket, "wait-for", "printed").returncode == 0
    result = _run("tmux_read", ["api", "50", "100"])
    assert result.exit_code == 0, result.stderr
    status, *lines = result.stdout.splitlines()
    assert lines == [f"line {i}" for i in range(4850, 4900)]
    assert " · truncated · older: tmux_read api 50 150" in status
    small = _run("tmux_read", ["api", "2000", "3000"]).stdout.splitlines()[0]
    assert "no lines of" in small and "truncated" not in small  # the bound did not bite


def test_four_parallel_starts_of_one_name_open_one_window(socket: str) -> None:
    def start(_: int) -> ExecutionResult:
        return _run("tmux_start", ["api", *_SLEEPER])

    with ThreadPoolExecutor(_PARALLEL) as pool:
        ends = list(pool.map(start, range(_PARALLEL)))

    assert sorted(end.exit_code for end in ends) == [0, 1, 1, 1], [e.stderr for e in ends]
    jobs = _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{@grim_job}").stdout.split()
    assert jobs == ["api"]
    # A loser either met the lock or, starting after the winner, the taken name.
    for loser in (end for end in ends if end.exit_code == 1):
        assert "name is being started" in loser.stderr or "already exists" in loser.stderr


def test_a_start_of_a_name_being_started_exits_1_and_opens_nothing(
    socket: str, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with (run_dir / f".tmux-start-{socket}-api.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the test is the other start
        result = _run("tmux_start", ["api", *_SLEEPER])
    assert result.exit_code == 1
    assert result.stderr == "tmux_start: name is being started\n"
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0  # the lock went with it
