"""Phase 3's literal done-when: a smoke suite of real tasks running
entirely through cli.main (never a raw subprocess in this test's own
orchestration — mirrors how the agent actually interacts), ending with
`grim run stats` reflecting the session's real activity.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from grim import cli
from grim.seeds.bodies import SEEDS


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    stdin: str = "",
) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    exit_code = cli.main(argv)
    return exit_code, capsys.readouterr().out


def test_ten_task_smoke_suite_and_stats_reflect_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    assert cli.main(["init"]) == 0  # seeds the library under session "human-adhoc"
    capsys.readouterr()

    monkeypatch.setenv("GRIM_SESSION", "test-session-1")

    exit_code, out = _run(monkeypatch, capsys, ["run", "shell", "--", "echo", "first"])
    assert exit_code == 0
    assert "first" in out

    exit_code, out = _run(monkeypatch, capsys, ["run", "shell", "--", "echo", "second"])
    assert exit_code == 0
    assert "second" in out

    exit_code, _ = _run(
        monkeypatch,
        capsys,
        ["write", "--name", "greet", "--lang", "python", "--desc", "prints a greeting"],
        stdin="print('hello there')",
    )
    assert exit_code == 0

    exit_code, out = _run(monkeypatch, capsys, ["run", "greet"])
    assert exit_code == 0
    assert "hello there" in out

    exit_code, _ = _run(
        monkeypatch,
        capsys,
        ["write", "--name", "double", "--lang", "python", "--desc", "doubles a number"],
        stdin="import sys\nprint(int(sys.argv[1]) * 2)",
    )
    assert exit_code == 0

    exit_code, out = _run(monkeypatch, capsys, ["run", "double", "--", "21"])
    assert exit_code == 0
    assert "42" in out

    target = tmp_path / "hello.txt"
    stdin_file = tmp_path / "stdin_for_write_file.txt"
    stdin_file.write_text("hello from grim\n")
    # `run` reads stdin via --stdin-file, unlike write/update which read
    # real stdin directly (verbs/run.py's cmd_run). --stdin-file must
    # come *before* the script name — cli.py's `args` positional is
    # nargs=REMAINDER, which swallows every token after `name` including
    # flags that look like this one; a pre-existing cli.py quirk, not
    # something Phase 3 fixes (see docs/build-plan.md).
    exit_code, _ = _run(
        monkeypatch, capsys, ["run", "--stdin-file", str(stdin_file), "write_file", str(target)]
    )
    assert exit_code == 0

    exit_code, out = _run(monkeypatch, capsys, ["run", "read_file", str(target)])
    assert exit_code == 0
    assert "hello from grim" in out

    exit_code, out = _run(monkeypatch, capsys, ["run", "list_dir", str(tmp_path)])
    assert exit_code == 0
    assert "hello.txt" in out

    exit_code, out = _run(monkeypatch, capsys, ["find", "greeting"])
    assert exit_code == 0
    assert "greet" in out

    exit_code, out = _run(monkeypatch, capsys, ["list"])
    assert exit_code == 0
    assert "greet" in out
    assert "shell" in out

    _assert_stats_reflect_the_session(monkeypatch, capsys)


def _assert_stats_reflect_the_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """7 execution rows precede this call (2 shell, 5 named-script runs);
    2 of those 7 are the shell escape hatch, 5 are reused seeds/scripts
    from an earlier session than "test-session-1". 6 distinct scripts were
    run (shell, greet, double, write_file, read_file, list_dir) out of
    len(SEEDS) + 2 unarchived (every seed plus greet and double); the active
    ratio is computed so adding a seed doesn't silently break this golden."""
    scripts_run = 6
    unarchived = len(SEEDS) + 2  # all seeds + greet + double
    expected_active = f"{scripts_run / unarchived:.2%}"
    exit_code, out = _run(monkeypatch, capsys, ["run", "stats"])
    assert exit_code == 0
    assert "total runs: 7" in out
    assert "shell-escape rate: 28.57%" in out
    assert "reuse rate: 71.43%" in out
    assert f"active library: {expected_active}" in out
