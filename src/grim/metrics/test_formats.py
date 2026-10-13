"""Tests for metrics/formats.py — every format, one small fixed dataset."""

from __future__ import annotations

import csv
import io
import json

import pytest

from grim.metrics.formats import FORMATS, TABLE_CELL_MAX_CHARS, Dataset, render

_DATASET = Dataset(
    name="demo",
    columns=("week", "runs", "fail_pct", "task"),
    rows=(
        {"week": "2026-W41", "runs": 12, "fail_pct": 8.3, "task": "fix | the build"},
        {"week": "2026-W42", "runs": 3, "fail_pct": None, "task": "x" * 60},
    ),
    meta={"since": "2026-10-01"},
)


def test_json_is_a_self_describing_envelope() -> None:
    data = json.loads(render(_DATASET, "json"))
    assert data["dataset"] == "demo"
    assert data["since"] == "2026-10-01"
    assert data["columns"] == ["week", "runs", "fail_pct", "task"]
    assert data["rows"][1] == {"week": "2026-W42", "runs": 3, "fail_pct": None, "task": "x" * 60}


def test_jsonl_is_one_untruncated_row_per_line() -> None:
    lines = render(_DATASET, "jsonl").splitlines()
    assert len(lines) == len(_DATASET.rows)
    assert json.loads(lines[1])["task"] == "x" * 60


@pytest.mark.parametrize(("fmt", "delimiter"), [("csv", ","), ("tsv", "\t")])
def test_delimited_formats_round_trip(fmt: str, delimiter: str) -> None:
    rows = list(csv.reader(io.StringIO(render(_DATASET, fmt)), delimiter=delimiter))
    assert rows[0] == ["week", "runs", "fail_pct", "task"]
    assert rows[1] == ["2026-W41", "12", "8.3", "fix | the build"]
    assert rows[2][2] == ""  # None -> empty field
    assert rows[2][3] == "x" * 60  # machine formats are never cut


def test_table_aligns_and_shortens_long_text() -> None:
    lines = render(_DATASET, "table").splitlines()
    assert lines[0].split() == ["week", "runs", "fail_pct", "task"]
    assert set(lines[1].replace(" ", "")) == {"-"}
    assert lines[2].index("12") > lines[0].index("runs")  # numbers right-aligned
    assert lines[3].endswith("x" * (TABLE_CELL_MAX_CHARS - 1) + "…")


def test_markdown_escapes_pipes_in_cells() -> None:
    lines = render(_DATASET, "md").splitlines()
    assert lines[0] == "| week | runs | fail_pct | task |"
    assert lines[1] == "|---|---|---|---|"
    assert "fix \\| the build" in lines[2]


def test_every_format_renders_an_empty_dataset() -> None:
    empty = Dataset(name="empty", columns=("a", "b"), rows=())
    for fmt in FORMATS:
        assert isinstance(render(empty, fmt), str)
