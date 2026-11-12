"""Runtime tests for the tmux job seeds in seeds/bodies_tmux.py (feature 1,
docs/features/01-jobs.md), dispatched exactly as `grim run` would. Every test
drives a private tmux server on its own socket ($GRIM_TMUX_SOCKET), so the
developer's own tmux is never touched; the fixture kills that server."""

from __future__ import annotations

import os
import re
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
_USAGE_EXIT_CODE = 2
_TAKEN_EXIT_CODE = 1
_JOB_EXIT_CODE = 3
_WAIT_TIMEOUT_EXIT_CODE = 124
_SLEEPER = ["sleep", "600"]  # outlives the test; the fixture's kill-server ends it
# A job that blocks until the test signals "go" on its tmux server (argv[1] is
# the socket), then exits 3 — so its death is a step in the test, never a race.
# Ref: https://man.openbsd.org/tmux#wait-for
_WAIT_THEN_EXIT = (
    "import subprocess, sys\n"
    "subprocess.run(['tmux', '-L', sys.argv[1], 'wait-for', 'go'], check=True)\n"
    f"sys.exit({_JOB_EXIT_CODE})\n"
)
_WINDOW = "#{window_name} #{@grim_kind} #{@grim_job} #{?pane_dead,dead,alive}"


def _run(name: str, argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    # tmp_path is unique per test and per pytest run, so sockets never collide.
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_TMUX_SOCKET", name)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    yield name
    _tmux(name, "kill-server")
    # A dead server leaves its socket file behind; remove it so runs leave no
    # litter. tmux keeps -L sockets in $TMUX_TMPDIR (or /tmp) under tmux-UID.
    # Ref: https://man.openbsd.org/tmux#L
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


def test_tmux_start_opens_a_tagged_window_and_returns_at_once(socket: str, tmp_path: Path) -> None:
    result = _run("tmux_start", ["api", *_SLEEPER])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"started grim:api\nlog: {tmp_path / 'run' / 'api.log'}\n"
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", _WINDOW).stdout == (
        "api job api alive\n"
    )


def test_tmux_start_splits_a_single_command_string(socket: str) -> None:
    assert _run("tmux_start", ["api", "sleep 600"]).exit_code == 0
    started = _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{pane_start_command}").stdout
    assert started.split() == _SLEEPER


def test_tmux_start_refuses_a_taken_job_name(socket: str) -> None:
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0
    again = _run("tmux_start", ["api", *_SLEEPER])
    assert again.exit_code == _TAKEN_EXIT_CODE
    assert "job 'api' already exists (live)" in again.stderr
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{window_name}").stdout == "api\n"


@pytest.mark.parametrize(
    "command",
    [["echo hi | cat"], ["echo", "hi", "&&", "ls"], ["sh", "-c", "echo hi"]],
    ids=["pipe", "and-chain", "sh-c"],
)
def test_tmux_start_refuses_shell_lines_and_starts_nothing(socket: str, command: list[str]) -> None:
    result = _run("tmux_start", ["api", *command])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "shell lines are refused" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0


@pytest.mark.parametrize(
    "argv", [[], ["api"], ["bad name", "sleep", "600"]], ids=["none", "no-program", "bad-name"]
)
def test_tmux_start_wrong_calls_exit_2_and_start_nothing(socket: str, argv: list[str]) -> None:
    result = _run("tmux_start", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "tmux_start" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0


def test_tmux_list_with_no_server_says_no_jobs(socket: str) -> None:
    result = _run("tmux_list", [])
    assert result.exit_code == 0
    assert result.stdout == "no jobs\n"


def test_tmux_list_rejects_arguments(socket: str) -> None:
    result = _run("tmux_list", ["api"])
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage: tmux_list" in result.stderr


def test_tmux_list_shows_live_and_dead_jobs_and_skips_other_windows(socket: str) -> None:
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0
    waiter = [sys.executable, "-c", _WAIT_THEN_EXIT, socket]
    assert _run("tmux_start", ["crawl", *waiter]).exit_code == 0
    # A window grim did not make: it carries no tags, so it is not a job.
    other = ["new-window", "-d", "-t", "=grim:", "-n", "mine", "cat", "-"]
    assert _tmux(socket, *other).returncode == 0
    assert _tmux(socket, "wait-for", "-S", "go").returncode == 0
    assert _tmux(socket, "wait-for", "grim-crawl").returncode == 0  # sent when crawl ends

    result = _run("tmux_list", [])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == len(["api", "crawl"]), result.stdout
    assert re.fullmatch(r"api  job  alive  age \d+s", lines[0])
    assert re.fullmatch(rf"crawl  job  dead \(exit {_JOB_EXIT_CODE}\)  age \d+s", lines[1])


def test_tmux_wait_returns_at_once_for_a_signal_sent_before_it(socket: str) -> None:
    assert _run("tmux_start", ["keep", *_SLEEPER]).exit_code == 0  # a server to signal on
    assert _tmux(socket, "wait-for", "-S", "grim-build").returncode == 0
    result = _run("tmux_wait", ["grim-build"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("signal 'grim-build' arrived after ")


def test_a_job_signals_when_it_ends_with_no_help_from_its_command(socket: str) -> None:
    waiter = [sys.executable, "-c", _WAIT_THEN_EXIT, socket]
    assert _run("tmux_start", ["build", *waiter]).exit_code == 0
    assert _tmux(socket, "wait-for", "-S", "go").returncode == 0
    assert _tmux(socket, "wait-for", "grim-build").returncode == 0  # the hook's own signal
    result = _run("tmux_wait", ["grim-build"])  # the signal is spent; the job is plainly dead
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"job 'build' is dead (exit {_JOB_EXIT_CODE}): nothing to wait for\n"


def test_tmux_wait_wakes_when_the_job_ends(socket: str) -> None:
    waiter = [sys.executable, "-c", _WAIT_THEN_EXIT, socket]
    assert _run("tmux_start", ["build", *waiter]).exit_code == 0
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(_run, "tmux_wait", ["grim-build", "15"])
        assert _tmux(socket, "wait-for", "-S", "go").returncode == 0
        result = blocked.result(timeout=_TIMEOUT_S)
    assert result.exit_code == 0, result.stderr
    assert f"job 'build' is dead (exit {_JOB_EXIT_CODE})" in result.stdout


def test_tmux_wait_times_out_with_124_and_says_the_job_still_runs(socket: str) -> None:
    assert _run("tmux_start", ["build", *_SLEEPER]).exit_code == 0
    result = _run("tmux_wait", ["grim-build", "0.2"])
    assert result.exit_code == _WAIT_TIMEOUT_EXIT_CODE
    assert "no signal 'grim-build' after 0.2s — job 'build' is still running" in result.stderr


def test_a_new_job_does_not_inherit_an_old_wake_of_its_name(socket: str) -> None:
    assert _run("tmux_start", ["keep", *_SLEEPER]).exit_code == 0
    assert _tmux(socket, "wait-for", "-S", "grim-api").returncode == 0  # a stale wake
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0
    assert _run("tmux_wait", ["grim-api", "0.2"]).exit_code == _WAIT_TIMEOUT_EXIT_CODE


@pytest.mark.parametrize(
    "argv",
    [[], ["grim-x", "0"], ["grim-x", "soon"], ["grim-x", "9999"], ["bad signal"]],
    ids=["none", "zero", "not-a-number", "over-cap", "bad-name"],
)
def test_tmux_wait_wrong_calls_exit_2(socket: str, argv: list[str]) -> None:
    result = _run("tmux_wait", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "usage: tmux_wait SIGNAL [SECONDS]" in result.stderr


# Jobs for the read and send tests. Each waits until tmux shows its own output
# (capture-pane, run inside the pane, where $TMUX names the right server)
# before it signals or exits, so a test never reads a half-drawn screen.
_SCREEN = "subprocess.run(['tmux', 'capture-pane', '-p'], capture_output=True, text=True).stdout"
_PRINT_5000 = (
    "import subprocess\n"
    "for i in range(5000):\n"
    "    print('line', i)\n"
    f"while 'line 4999' not in {_SCREEN}:\n"
    "    pass\n"
    "subprocess.run(['tmux', 'wait-for', '-S', 'printed'])\n"
    "subprocess.run(['tmux', 'wait-for', 'hold'])\n"
)
_PROMPT = (
    "import subprocess\n"
    "while True:\n"
    "    answer = str(eval(input('> ')))\n"
    "    print(answer)\n"
    f"    while answer not in {_SCREEN}:\n"
    "        pass\n"
    "    subprocess.run(['tmux', 'wait-for', '-S', 'answered'])\n"
)
# Exits only once its words are in the job log, so the log is never read early.
_LAST_WORDS = (
    "import os, pathlib, sys\n"
    "print('last words', flush=True)\n"
    "log = pathlib.Path(os.environ['GRIM_RUN_DIR'], 'api.log')\n"
    "while 'last words' not in log.read_text():\n"
    "    pass\n"
    f"sys.exit({_JOB_EXIT_CODE})\n"
)


def test_tmux_read_gets_the_last_lines_and_says_how_to_page_back(socket: str) -> None:
    assert _run("tmux_start", ["api", sys.executable, "-c", _PRINT_5000]).exit_code == 0
    assert _tmux(socket, "wait-for", "printed").returncode == 0

    result = _run("tmux_read", ["api", "50"])

    assert result.exit_code == 0, result.stderr
    status, *lines = result.stdout.splitlines()
    assert lines == [f"line {i}" for i in range(4950, 5000)]
    assert status.startswith("[tmux_read] api: alive · lines ")
    assert " · older: tmux_read api 50 50 · full log: " in status
    older = _run("tmux_read", ["api", "50", "50"]).stdout.splitlines()[1:]
    assert older == [f"line {i}" for i in range(4900, 4950)]


def test_tmux_send_types_text_as_is_then_presses_enter(socket: str) -> None:
    assert _run("tmux_start", ["repl", sys.executable, "-c", _PROMPT]).exit_code == 0
    for text in ["1 + 1", "'Enter ' + 'C-c'"]:  # key names in TEXT stay plain letters
        assert _run("tmux_send", ["repl", text]).exit_code == 0
        assert _tmux(socket, "wait-for", "answered").returncode == 0
    screen = [line.rstrip() for line in _run("tmux_read", ["repl"]).stdout.splitlines()[1:]]
    assert screen == ["> 1 + 1", "2", "> 'Enter ' + 'C-c'", "Enter C-c", ">"]


def test_tmux_read_of_a_dead_job_gives_its_last_words_and_exit_code(socket: str) -> None:
    assert _run("tmux_start", ["api", sys.executable, "-c", _LAST_WORDS]).exit_code == 0
    assert _tmux(socket, "wait-for", "grim-api").returncode == 0
    result = _run("tmux_read", ["api"])
    assert result.exit_code == 0, result.stderr
    status, *lines = result.stdout.splitlines()
    assert lines == ["last words"]
    assert f"api: dead (exit {_JOB_EXIT_CODE}) · lines 1-1 of 1 from its log" in status


def test_tmux_send_refuses_a_dead_job(socket: str) -> None:
    waiter = [sys.executable, "-c", _WAIT_THEN_EXIT, socket]
    assert _run("tmux_start", ["api", *waiter]).exit_code == 0
    assert _tmux(socket, "wait-for", "-S", "go").returncode == 0
    assert _tmux(socket, "wait-for", "grim-api").returncode == 0
    result = _run("tmux_send", ["api", "hello"])
    assert result.exit_code == _TAKEN_EXIT_CODE
    assert f"job 'api' is dead (exit {_JOB_EXIT_CODE})" in result.stderr


def test_tmux_read_and_send_name_an_unknown_job(socket: str) -> None:
    assert _run("tmux_start", ["keep", *_SLEEPER]).exit_code == 0
    for name, argv in [("tmux_read", ["ghost"]), ("tmux_send", ["ghost", "hi"])]:
        result = _run(name, argv)
        assert result.exit_code == _TAKEN_EXIT_CODE
        assert "no such job: 'ghost' (jobs: keep)" in result.stderr


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("tmux_read", []),
        ("tmux_read", ["api", "0"]),
        ("tmux_read", ["api", "many"]),
        ("tmux_read", ["api", "2001"]),
        ("tmux_send", ["api"]),
        ("tmux_send", ["api", "two", "words"]),
    ],
)
def test_tmux_read_and_send_wrong_calls_exit_2(socket: str, name: str, argv: list[str]) -> None:
    result = _run(name, argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert f"usage: {name} JOB" in result.stderr


def test_tmux_stop_closes_the_window_and_tmux_list_forgets_the_job(
    socket: str, tmp_path: Path
) -> None:
    for job in ["api", "keep"]:
        assert _run("tmux_start", [job, *_SLEEPER]).exit_code == 0
    result = _run("tmux_stop", ["api"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "stopped grim:api (it was alive)\n"
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{window_name}").stdout == "keep\n"
    assert _run("tmux_list", []).stdout.startswith("keep  job  alive")
    assert (tmp_path / "run" / "api.log").is_file()  # the log outlives the job


def test_tmux_stop_of_the_only_job_leaves_no_jobs(socket: str) -> None:
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0
    assert _run("tmux_stop", ["api"]).exit_code == 0
    assert _run("tmux_list", []).stdout == "no jobs\n"


def test_tmux_stop_clears_a_dead_job_so_its_name_is_free_again(socket: str) -> None:
    waiter = [sys.executable, "-c", _WAIT_THEN_EXIT, socket]
    assert _run("tmux_start", ["api", *waiter]).exit_code == 0
    assert _run("tmux_start", ["keep", *_SLEEPER]).exit_code == 0
    assert _tmux(socket, "wait-for", "-S", "go").returncode == 0
    assert _tmux(socket, "wait-for", "grim-api").returncode == 0
    result = _run("tmux_stop", ["api"])
    assert result.stdout == f"stopped grim:api (it was dead (exit {_JOB_EXIT_CODE}))\n"
    assert _run("tmux_start", ["api", *_SLEEPER]).exit_code == 0


def test_tmux_stop_wakes_a_blocked_tmux_wait(socket: str) -> None:
    for job in ["api", "keep"]:  # "keep" holds the server up after api is gone
        assert _run("tmux_start", [job, *_SLEEPER]).exit_code == 0
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(_run, "tmux_wait", ["grim-api", "15"])
        assert _run("tmux_stop", ["api"]).exit_code == 0
        result = blocked.result(timeout=_TIMEOUT_S)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("signal 'grim-api' arrived after ")


def test_tmux_stop_wrong_calls_and_unknown_jobs_fail_loudly(socket: str) -> None:
    assert _run("tmux_start", ["keep", *_SLEEPER]).exit_code == 0
    for argv in [[], ["api", "extra"]]:
        result = _run("tmux_stop", argv)
        assert result.exit_code == _USAGE_EXIT_CODE
        assert "usage: tmux_stop JOB" in result.stderr
    unknown = _run("tmux_stop", ["ghost"])
    assert unknown.exit_code == _TAKEN_EXIT_CODE
    assert "no such job: 'ghost' (jobs: keep)" in unknown.stderr
