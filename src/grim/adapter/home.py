"""Where grim-agent runs (docs/features/03-dashboard.md). An attended run that
starts outside tmux moves into tmux session `grim`, window `agent`, with the
terminal attached — the same session its jobs and delegates open in, so
`tmux attach -t grim` shows everything grim drives. Already inside tmux, or
headless (print mode, a bot, launchd), the agent stays where it is.

The caller's environment reaches the new window by NAME only: tmux copies the
variables named in a session's `update-environment` from the attaching client,
so no value (an API key, say) is ever put in an argv.
Ref: https://man.openbsd.org/tmux#update-environment
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping

SESSION = "grim"
AGENT_WINDOW = "agent"
BOOT_WINDOW = "grim-boot"
HOME_ENV = "GRIM_HOME"  # 0/off/no/false: never move into tmux
SOCKET_ENV = "GRIM_TMUX_SOCKET"  # a private tmux server, as the tmux seeds use
_OFF = frozenset({"0", "off", "no", "false"})
# Per-terminal or per-shell state: the window must get its own, not the caller's.
_NOT_CARRIED = frozenset({"TERM", "TERMCAP", "PWD", "OLDPWD", "SHLVL", "_", "COLUMNS", "LINES"})
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_CARRIED = 2000  # names in one update-environment value; far above any real shell


def stays_here(env: Mapping[str, str], attended: bool, tmux_found: bool) -> bool:
    """True when the agent runs in the current terminal or pane: it is headless,
    already inside tmux, tmux is missing, or $GRIM_HOME turned the move off."""
    assert isinstance(attended, bool), "attended is a plain yes/no"
    assert isinstance(tmux_found, bool), "tmux_found is a plain yes/no"
    opted_out = env.get(HOME_ENV, "").strip().lower() in _OFF
    return not attended or not tmux_found or opted_out or bool(env.get("TMUX"))


def carried_names(env: Mapping[str, str]) -> list[str]:
    """Names (never values) of the variables the agent's window should inherit."""
    names = sorted(
        name
        for name in env
        if _ENV_NAME.fullmatch(name) and name not in _NOT_CARRIED and not name.startswith("TMUX")
    )
    assert len(names) <= _MAX_CARRIED, f"{len(names)} env names exceeds the cap {_MAX_CARRIED}"
    assert all(" " not in name for name in names), "names are space-separated in the option"
    return names


def _tmux(env: Mapping[str, str]) -> list[str]:
    socket = env.get(SOCKET_ENV)
    base = ["tmux", "-L", socket] if socket else ["tmux"]
    assert base[0] == "tmux", "every home command is a tmux call"
    assert len(base) in (1, 3), "tmux alone, or tmux -L SOCKET"
    return base


def _escape(arg: str) -> str:
    """tmux reads a trailing ';' on an argument as a command separator; '\\;'
    keeps it literal. Ref: https://man.openbsd.org/tmux#COMMAND_PARSING_AND_EXECUTION"""
    escaped = arg[:-1] + "\\;" if arg.endswith(";") else arg
    assert not escaped.endswith(";") or escaped.endswith("\\;"), "no bare trailing separator"
    assert len(escaped) >= len(arg), "escaping never drops text"
    return escaped


def home_command(
    agent_argv: list[str], env: Mapping[str, str], cwd: str, session_exists: bool
) -> list[str]:
    """The single tmux call that moves the agent home: (make session `grim` if
    it is missing, on a placeholder window), name the env to carry, attach this
    terminal, open window `agent` running the agent, tag it, drop the placeholder."""
    assert agent_argv, "the agent's own argv is never empty"
    assert cwd, "the agent's working directory is known"
    target = f"={SESSION}"  # exact match: never a session merely named grim-something
    steps: list[list[str]] = []
    if not session_exists:
        boot = ["new-session", "-d", "-s", SESSION, "-n", BOOT_WINDOW, "-c", cwd, "cat", "-"]
        steps.append(boot)
    agent = [_escape(arg) for arg in agent_argv]
    steps += [
        # set-option wants the "session:" form; a bare "=grim" is "no such session".
        ["set-option", "-t", f"{target}:", "update-environment", " ".join(carried_names(env))],
        ["attach-session", "-t", target],
        ["new-window", "-t", f"{target}:", "-n", AGENT_WINDOW, "-c", cwd, "--", *agent],
        ["set-option", "-w", "@grim_kind", "agent"],
    ]
    if not session_exists:
        steps.append(["kill-window", "-t", f"{target}:{BOOT_WINDOW}"])
    command = _tmux(env)
    for index, step in enumerate(steps):
        command += [";", *step] if index else step
    assert command.count("attach-session") == 1, "the terminal is attached exactly once"
    return command


def agent_program() -> str | None:
    """The grim-agent executable to re-run inside the window, or None."""
    current = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    is_agent = os.path.basename(current) == "grim-agent" and os.access(current, os.X_OK)
    found = current if is_agent else shutil.which("grim-agent")
    assert found is None or os.path.isabs(found), "the program is an absolute path"
    assert found is None or found.endswith("grim-agent"), "only grim-agent is re-run"
    return found


def go_home(user_argv: list[str], print_mode: bool) -> None:
    """Replace this process with the tmux client that moves the agent home, or
    return at once when it should run right here. Call before any setup."""
    assert isinstance(user_argv, list), "argv must be a list"
    assert isinstance(print_mode, bool), "print_mode is a plain yes/no"
    # Ref: https://docs.python.org/3/library/io.html#io.IOBase.isatty
    attended = sys.stdin.isatty() and sys.stdout.isatty() and not print_mode
    program = agent_program()
    if program is None or stays_here(os.environ, attended, shutil.which("tmux") is not None):
        return
    probe = [*_tmux(os.environ), "has-session", "-t", f"={SESSION}"]
    exists = subprocess.run(probe, capture_output=True, check=False).returncode == 0
    command = home_command([program, *user_argv], os.environ, os.getcwd(), exists)
    # Ref: https://docs.python.org/3/library/os.html#os.execvp (never returns)
    os.execvp(command[0], command)
