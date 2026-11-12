"""Tests for adapter/home.py (docs/features/03-dashboard.md): when grim-agent
moves into tmux session `grim`, and the one tmux call that does it. The last
test runs that call for real, on a private tmux server, through a pty."""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from grim.adapter import home

_AGENT = ["/opt/bin/grim-agent", "fix the tests"]
_TIMEOUT_S = 20.0


@pytest.mark.parametrize(
    ("env", "attended", "tmux_found", "stays"),
    [
        ({}, True, True, False),  # a terminal outside tmux: go home
        ({"TMUX": "/tmp/tmux-501/default,1,0"}, True, True, True),  # already inside tmux
        ({}, False, True, True),  # headless: print mode, a bot, launchd
        ({}, True, False, True),  # no tmux on this machine
        ({"GRIM_HOME": "0"}, True, True, True),
        ({"GRIM_HOME": " Off "}, True, True, True),
        ({"GRIM_HOME": "1"}, True, True, False),
    ],
)
def test_stays_here(env: dict[str, str], attended: bool, tmux_found: bool, stays: bool) -> None:
    assert home.stays_here(env, attended, tmux_found) is stays


def test_carried_names_are_names_only_and_skip_terminal_state() -> None:
    env = {"API_TOKEN": "s3cret", "GRIM_MODEL": "m", "TERM": "xterm", "TMUX_PANE": "%1", "a-b": "x"}
    assert home.carried_names(env) == ["API_TOKEN", "GRIM_MODEL"]


def test_home_command_for_a_new_session_never_holds_a_value() -> None:
    env = {"API_TOKEN": "s3cret", "GRIM_TMUX_SOCKET": "private"}
    command = home.home_command(_AGENT, env, "/work", session_exists=False)
    assert command[:3] == ["tmux", "-L", "private"]
    names = [step.split()[0] for step in " ".join(command[3:]).split(" ; ")]
    assert names == [
        "new-session",
        "set-option",
        "attach-session",
        "new-window",
        "set-option",
        "kill-window",
    ]
    assert "s3cret" not in " ".join(command)  # a secret is named, never passed
    assert command[command.index("--") + 1 :][:2] == _AGENT


def test_home_command_for_a_live_session_only_adds_a_window() -> None:
    command = home.home_command(_AGENT, {}, "/work", session_exists=True)
    assert command[0] == "tmux"
    assert "new-session" not in command and "kill-window" not in command
    assert command.count("new-window") == 1


def test_home_command_keeps_a_trailing_semicolon_as_text() -> None:
    command = home.home_command(["/opt/bin/grim-agent", "do it;"], {}, "/work", True)
    assert command[-6] == r"do it\;"  # then: ; set-option -w @grim_kind agent
    assert command[-5:] == [";", "set-option", "-w", "@grim_kind", "agent"]


def test_go_home_returns_when_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object) -> None:
        raise AssertionError("a headless run must never exec tmux")

    monkeypatch.setattr(os, "execvp", refuse)
    monkeypatch.delenv("TMUX", raising=False)
    home.go_home(["fix the tests"], print_mode=True)  # print mode: stays, no window
    home.go_home(["fix the tests"], print_mode=False)  # pytest's stdin is not a terminal


class _Terminal:
    def isatty(self) -> bool:
        return True


def test_go_home_execs_the_home_command_when_attended(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(sys, "stdout", _Terminal())
    monkeypatch.setattr(home, "agent_program", lambda: "/opt/bin/grim-agent")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tmux")
    monkeypatch.setattr(os, "execvp", lambda _file, argv: seen.append(argv))
    monkeypatch.setenv("GRIM_TMUX_SOCKET", "grimtest-none")  # has-session finds no server
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("GRIM_HOME", raising=False)

    home.go_home(["fix the tests"], print_mode=False)

    assert len(seen) == 1
    assert seen[0][:4] == ["tmux", "-L", "grimtest-none", "new-session"]
    assert seen[0][seen[0].index("--") + 1 :][:2] == ["/opt/bin/grim-agent", "fix the tests"]


# Stands in for grim-agent inside the window: waits for the test's "go", then
# reports what it can see (an env var of the caller's, its window, its tag).
_PROBE = (
    "import os, pathlib, subprocess, sys\n"
    "subprocess.run(['tmux', 'wait-for', 'go'], check=True)\n"
    "where = subprocess.run(['tmux', 'display-message', '-p', '#S:#W #{@grim_kind}'],\n"
    "                       capture_output=True, text=True).stdout.strip()\n"
    "pathlib.Path(sys.argv[1]).write_text(os.environ.get('GRIM_PROBE', 'MISSING') + ' ' + where)\n"
    "subprocess.run(['tmux', 'wait-for', '-S', 'seen'], check=True)\n"
)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="drives a real tmux")
def test_home_command_runs_the_agent_in_window_agent_with_the_callers_env(tmp_path: Path) -> None:
    socket = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    tmux = ["tmux", "-L", socket]
    bare = {"PATH": os.environ["PATH"], "TERM": "xterm-256color", "HOME": str(tmp_path)}
    # A server that is already up and has never seen GRIM_PROBE.
    subprocess.run([*tmux, "new-session", "-d", "-s", "other", "cat", "-"], env=bare, check=True)
    seen = tmp_path / "seen.txt"
    caller = {**bare, "GRIM_TMUX_SOCKET": socket, "GRIM_PROBE": "carried"}
    agent = [sys.executable, "-c", _PROBE, str(seen)]
    command = home.home_command(agent, caller, str(tmp_path), session_exists=False)
    # tmux attaches only to a terminal, so the client gets a pty.
    # Ref: https://docs.python.org/3/library/pty.html#pty.openpty
    master, slave = pty.openpty()
    client = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=caller)
    os.close(slave)
    drain = threading.Thread(target=_drain, args=(master,), daemon=True)
    drain.start()
    try:
        for step in (["wait-for", "-S", "go"], ["wait-for", "seen"]):
            subprocess.run([*tmux, *step], check=True, timeout=_TIMEOUT_S)
        assert seen.read_text() == "carried grim:agent agent"
        windows = subprocess.run(
            [*tmux, "list-windows", "-t", "=grim", "-F", "#W"], capture_output=True, text=True
        ).stdout.split()
        assert home.BOOT_WINDOW not in windows  # the placeholder is gone
    finally:
        subprocess.run([*tmux, "kill-server"], capture_output=True, check=False)
        client.wait(timeout=_TIMEOUT_S)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
        (socket_dir / socket).unlink(missing_ok=True)


def _drain(master: int) -> None:
    """Read the pty until the client exits, so tmux never blocks on a full buffer."""
    try:
        while os.read(master, 65536):
            pass
    except OSError:
        pass  # the client closed its end: nothing more to read
    finally:
        os.close(master)
