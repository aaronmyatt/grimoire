"""docs/features/07-recipes.md, the two scenarios that need no model: a recipe
(a named python script) drives a surface by calling the driver seeds through
`grim run`, checking each exit code — and every one of those calls is left on
the record as an execution row. Runs through cli.main, as the agent does, on
a throwaway library and a private tmux server."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim import cli

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="the recipe drives tmux")

_SESSION = "recipe-session"
# The recipe: start a job, block until it ends, print its last words, clear it.
# Seeds are reached with `grim run`, never imported, and every call is checked.
_RECIPE = '''"""job_report — run a program as a tmux job, wait for it, print what it said.
Usage: job_report JOB PROGRAM [ARG...]"""
import subprocess
import sys


def seed(name, *args):
    done = subprocess.run(["grim", "run", name, "--", *args], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"job_report: {name} failed (exit {done.returncode}): {done.stderr.strip()}")
    return done.stdout


job, program = sys.argv[1], sys.argv[2:]
seed("tmux_start", job, *program)
seed("tmux_wait", f"grim-{job}", "20")
print(seed("tmux_read", job))
seed("tmux_stop", job)
'''
# The job says its line, then exits once that line is in its own log, so the
# recipe's read of the dead job never comes early.
_JOB = (
    "import os, pathlib\n"
    "print('recipe says hi', flush=True)\n"
    "log = pathlib.Path(os.environ['GRIM_RUN_DIR'], 'build.log')\n"
    "while 'recipe says hi' not in log.read_text():\n"
    "    pass\n"
)


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    socket = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setenv("GRIM_TMUX_SOCKET", socket)
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    assert cli.main(["init"]) == 0
    monkeypatch.setenv("GRIM_SESSION", _SESSION)
    yield tmp_path / "grimoire.db"
    subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / socket).unlink(missing_ok=True)


def test_a_recipe_drives_tmux_through_grim_run_and_every_call_is_on_the_record(
    library: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_RECIPE))
    described = "run a program as a tmux job, wait for it to end, print what it said"
    assert cli.main(["write", "--name", "job_report", "--lang", "python", "--desc", described]) == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    exit_code = cli.main(["run", "job_report", "--", "build", "python3", "-c", _JOB])

    assert exit_code == 0, capsys.readouterr().out
    assert "recipe says hi" in capsys.readouterr().out
    conn = sqlite3.connect(library)
    rows = conn.execute(
        "SELECT s.name, e.argv, e.exit_code, e.duration_ms, e.started_at FROM execution e "
        "JOIN script_version sv ON sv.id = e.script_version_id "
        "JOIN script s ON s.id = sv.script_id WHERE e.session_id = ? ORDER BY e.started_at, e.id",
        (_SESSION,),
    ).fetchall()
    conn.close()
    by_name = {name: (json.loads(argv), code, ms, at) for name, argv, code, ms, at in rows}
    assert set(by_name) == {"job_report", "tmux_start", "tmux_wait", "tmux_read", "tmux_stop"}
    assert by_name["tmux_start"][0] == ["build", "python3", "-c", _JOB]  # the argv, as sent
    assert by_name["tmux_wait"][0] == ["grim-build", "20"]
    assert all(code == 0 and ms >= 0 and at for _argv, code, ms, at in by_name.values())


def test_a_recipe_stops_at_the_first_seed_that_fails(
    library: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(_RECIPE))
    assert cli.main(["write", "--name", "job_report", "--lang", "python", "--desc", "x y z"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    exit_code = cli.main(["run", "job_report", "--", "build", "sh", "-c", "echo hi"])
    assert exit_code != 0
    assert "job_report: tmux_start failed (exit 2)" in capsys.readouterr().out
