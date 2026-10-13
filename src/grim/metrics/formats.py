"""Output formats for `grim stats` (metrics slice): one Dataset in, text out.

Pure functions only — no DB, no files. Machine formats (json, jsonl, csv,
tsv) carry every value untouched, so a later visualisation layer can read
them; human formats (table, md) shorten long text cells for the terminal.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

Cell = str | int | float | None

FORMATS = ("table", "json", "jsonl", "csv", "tsv", "md")
TABLE_CELL_MAX_CHARS = 40  # human formats only; machine formats are never cut


@dataclass(frozen=True)
class Dataset:
    """A named, rectangular table: every row has exactly `columns` as keys.
    `meta` (e.g. generated_at, since) rides along in the json envelope."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Cell], ...]
    meta: dict[str, str] = field(default_factory=dict)


def render(dataset: Dataset, fmt: str) -> str:
    """Text for `dataset` in format `fmt` (one of FORMATS), no trailing newline."""
    assert fmt in FORMATS, f"unknown format {fmt!r}"
    assert all(tuple(row) == dataset.columns for row in dataset.rows), "rows match columns"
    renderers = {
        "table": _table,
        "json": _json,
        "jsonl": _jsonl,
        "csv": lambda d: _delimited(d, ","),
        "tsv": lambda d: _delimited(d, "\t"),
        "md": _markdown,
    }
    return renderers[fmt](dataset)


def _json(dataset: Dataset) -> str:
    """Self-describing envelope: name, meta, column order, then the rows.
    Ref: https://docs.python.org/3/library/json.html#json.dumps"""
    envelope = {
        "dataset": dataset.name,
        **dataset.meta,
        "columns": list(dataset.columns),
        "rows": list(dataset.rows),
    }
    return json.dumps(envelope, indent=2)


def _jsonl(dataset: Dataset) -> str:
    """One JSON object per row — streamable, appendable."""
    return "\n".join(json.dumps(row) for row in dataset.rows)


def _delimited(dataset: Dataset, delimiter: str) -> str:
    """CSV/TSV with a header row; None becomes an empty field.
    Ref: https://docs.python.org/3/library/csv.html#csv.writer"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(dataset.columns)
    for row in dataset.rows:
        writer.writerow(["" if row[c] is None else row[c] for c in dataset.columns])
    return buffer.getvalue().rstrip("\n")


def _short(value: Cell) -> str:
    """A cell as one short line for the human formats."""
    text = "" if value is None else " ".join(str(value).split())
    if len(text) > TABLE_CELL_MAX_CHARS:
        text = text[: TABLE_CELL_MAX_CHARS - 1] + "…"
    assert len(text) <= TABLE_CELL_MAX_CHARS, "human cells respect their cap"
    return text


def _table(dataset: Dataset) -> str:
    """Aligned columns: numbers right-aligned, text left-aligned."""
    cells = [[_short(row[c]) for c in dataset.columns] for row in dataset.rows]
    widths = [
        max([len(name)] + [len(line[i]) for line in cells])
        for i, name in enumerate(dataset.columns)
    ]
    numeric = [
        all(isinstance(row[c], int | float) or row[c] is None for row in dataset.rows)
        for c in dataset.columns
    ]

    def line(values: list[str]) -> str:
        parts = [
            v.rjust(w) if num else v.ljust(w)
            for v, w, num in zip(values, widths, numeric, strict=True)
        ]
        return "  ".join(parts).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return "\n".join([line(list(dataset.columns)), rule, *(line(c) for c in cells)])


def _markdown(dataset: Dataset) -> str:
    """A GitHub-flavoured markdown table; `|` in cells is escaped."""

    def line(values: list[str]) -> str:
        return "| " + " | ".join(v.replace("|", "\\|") for v in values) + " |"

    header = line(list(dataset.columns))
    rule = "|" + "|".join("---" for _ in dataset.columns) + "|"
    body = [line([_short(row[c]) for c in dataset.columns]) for row in dataset.rows]
    return "\n".join([header, rule, *body])
