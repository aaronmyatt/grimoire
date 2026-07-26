"""End-to-end exercise of the six verbs through `cli.main` — Phase 1's
literal done-when (build plan Phase 1): write -> find -> read -> run ->
update -> run@old-version on a fresh DB.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from grim import cli


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: str = "") -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(argv)


def test_write_find_read_run_update_run_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    assert cli.main(["init"]) == 0

    exit_code = _run_cli(
        monkeypatch,
        ["write", "--name", "greet", "--lang", "python", "--desc", "prints a greeting"],
        stdin="print('hello v1')",
    )
    assert exit_code == 0

    assert _run_cli(monkeypatch, ["find", "greeting"]) == 0
    assert "greet" in capsys.readouterr().out

    assert _run_cli(monkeypatch, ["read", "greet"]) == 0
    assert "hello v1" in capsys.readouterr().out

    assert _run_cli(monkeypatch, ["run", "greet"]) == 0
    assert "hello v1" in capsys.readouterr().out

    exit_code = _run_cli(
        monkeypatch, ["update", "greet", "--changelog", "say v2"], stdin="print('hello v2')"
    )
    assert exit_code == 0

    assert _run_cli(monkeypatch, ["run", "greet@1"]) == 0
    assert "hello v1" in capsys.readouterr().out


def test_write_rejects_invalid_python_with_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    assert cli.main(["init"]) == 0

    exit_code = _run_cli(
        monkeypatch,
        ["write", "--name", "broken", "--lang", "python", "--desc", "d"],
        stdin="def broken(:\n    pass",
    )

    assert exit_code == 1
    assert "syntax error" in capsys.readouterr().err
