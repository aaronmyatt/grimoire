"""`grim stats` (metrics slice): print one dataset in one format.

    grim stats [summary|weekly|sessions|runs|scripts] [--format FMT] [--since YYYY-MM-DD]

Human-facing, like the curate commands: never on the agent's tool surface,
but any script can shell out to it (`subprocess.run(["grim", "stats", ...])`)
— which is how a visualisation layer reads raw numbers. Read-only.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from grim import db
from grim.metrics import datasets, formats, spans

USAGE_EXIT_CODE = 2
NO_LIBRARY_EXIT_CODE = 1


def configure(parser: argparse.ArgumentParser) -> None:
    """Add `grim stats` arguments to its subparser (cli.py calls this, so
    the choices live beside the code that implements them)."""
    parser.add_argument(
        "dataset", nargs="?", default="summary", choices=datasets.DATASETS,
        help="summary/weekly roll up runs/sessions/scripts (the raw rows)",
    )  # fmt: skip
    parser.add_argument("--format", default="table", choices=formats.FORMATS, dest="fmt")
    parser.add_argument("--since", default="", help="only data from this UTC date (YYYY-MM-DD)")
    parser.set_defaults(func=cmd_stats)
    assert parser.get_default("func") is cmd_stats, "stats dispatches to cmd_stats"


def cmd_stats(args: argparse.Namespace) -> int:
    """Build the dataset and print it. Exit 2 on a bad --since, 1 when there
    is no library yet; 0 otherwise (a closed pipe is not an error)."""
    since = str(args.since or "").strip()
    try:
        if since:
            time.strptime(since, "%Y-%m-%d")
    except ValueError:
        print(f"error: --since must be YYYY-MM-DD, got {since!r}", file=sys.stderr)
        return USAGE_EXIT_CODE
    db_path = db.resolve_db_path()
    if not db_path.is_file():
        print(f"error: no library at {db_path} — run `grim init` first", file=sys.stderr)
        return NO_LIBRARY_EXIT_CODE
    conn = db.connect(db_path)
    try:
        source = datasets.Source(conn, spans.instrument_path(), since, _now())
        text = formats.render(datasets.build(args.dataset, source), args.fmt)
    finally:
        conn.close()
    return _emit(text)


def _now() -> str:
    """Current UTC time in the library's timestamp format (patched in tests)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _emit(text: str) -> int:
    """Print `text`; a reader that stops early (`| head`) is fine, not a crash.
    Ref: https://docs.python.org/3/library/signal.html#note-on-sigpipe"""
    try:
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    return 0
