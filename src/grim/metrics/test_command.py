"""Tests for metrics/command.py — `grim stats` end to end through argparse,
against a tmp library and span log (GRIM_DB / GRIM_INSTRUMENT_FILE)."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import pytest

from grim import db
from grim.metrics import command

_NOW = "2026-10-15 00:00:00"


@pytest.fixture
def parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.ArgumentParser:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setenv("GRIM_INSTRUMENT_FILE", str(tmp_path / "absent.jsonl"))
    monkeypatch.setattr(command, "_now", lambda: _NOW)
    stats = argparse.ArgumentParser(prog="grim stats")
    command.configure(stats)
    return stats


def _library(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "grimoire.db")
    db.migrate(conn)
    conn.executescript(
        "INSERT INTO session (id, kind) VALUES ('s1', 'agent');"
        "INSERT INTO script (id, name, language, description) VALUES (1, 'greet', 'python', 'd');"
        "INSERT INTO script_version (id, script_id, version, body, body_hash) "
        "VALUES (1, 1, 1, 'b', 'h');"
        "INSERT INTO execution (script_version_id, session_id, seq, exit_code) "
        "VALUES (1, 's1', 1, 0);"
    )
    conn.close()


def _run(parser: argparse.ArgumentParser, argv: list[str]) -> int:
    args = parser.parse_args(argv)
    code: int = args.func(args)
    return code


def test_default_is_the_summary_table(
    parser: argparse.ArgumentParser, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _library(tmp_path)
    assert _run(parser, []) == 0
    header = capsys.readouterr().out.splitlines()[0].split()
    assert header[:3] == ["period", "runs", "failed_runs"]


def test_json_and_csv_carry_the_raw_rows(
    parser: argparse.ArgumentParser, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _library(tmp_path)
    assert _run(parser, ["runs", "--format", "json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert (envelope["dataset"], envelope["generated_at"]) == ("runs", _NOW)
    assert envelope["rows"][0]["script"] == "greet"
    assert _run(parser, ["scripts", "--format", "csv"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert (rows[0]["script"], rows[0]["runs"]) == ("greet", "1")


def test_bad_since_is_a_usage_error(
    parser: argparse.ArgumentParser, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _library(tmp_path)
    assert _run(parser, ["--since", "last week"]) == command.USAGE_EXIT_CODE
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_missing_library_says_run_init(
    parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(parser, ["weekly"]) == command.NO_LIBRARY_EXIT_CODE
    assert "grim init" in capsys.readouterr().err


def test_unknown_dataset_or_format_is_rejected_by_argparse(
    parser: argparse.ArgumentParser,
) -> None:
    for argv in (["everything"], ["--format", "xml"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
