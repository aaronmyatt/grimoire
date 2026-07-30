"""`grim` command-line entry point — argparse dispatch to cmd_* handlers.

Frozen path (root CLAUDE.md §5): human-confirmed on every write, its own
commit. Wires the six agent-facing verbs (build plan §4, §12/D12) to their
implementations in src/grim/verbs/ — this module owns argument shapes
only, never verb logic.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import TYPE_CHECKING

from grim import db
from grim.seeds.bodies import SEEDS
from grim.seeds.loader import load_seeds
from grim.verbs import find, read, run, update, write
from grim.verbs import list as list_verb

if TYPE_CHECKING:
    # argparse._SubParsersAction isn't subscriptable at runtime (only under
    # typing), so this alias must never be evaluated outside type checkers.
    _SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    _SubParsers = argparse._SubParsersAction


def cmd_init(args: argparse.Namespace) -> int:
    """`grim init`: connect + migrate + seed the library, reporting what
    was newly applied/seeded (build plan Phase 3)."""
    conn = db.connect()
    applied = db.migrate(conn)
    print(f"applied: {', '.join(applied)}" if applied else "already up to date")

    conn.row_factory = sqlite3.Row
    newly_seeded = load_seeds(conn)
    if newly_seeded:
        print(f"seeded: {', '.join(newly_seeded)}")
    else:
        print(f"seeds already present ({len(SEEDS)})")

    assert isinstance(applied, list), "migrate() must always return a list"
    assert conn is not None, "connect() must always return a connection or raise"
    return 0


def _add_write_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("write", help="create a new script (body on stdin)")
    parser.add_argument("--name", required=True)
    parser.add_argument("--lang", required=True)
    parser.add_argument("--desc", required=True)
    parser.add_argument("--parent")
    parser.add_argument("--scope")
    parser.set_defaults(func=write.cmd_write)


def _add_update_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("update", help="append a new version (body on stdin)")
    parser.add_argument("name")
    parser.add_argument("--changelog", required=True)
    parser.set_defaults(func=update.cmd_update)


def _add_read_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("read", help="show a script, or page an execution's output")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--exec", type=int, dest="exec")
    parser.add_argument("--page", type=int)
    parser.set_defaults(func=read.cmd_read)


def _add_list_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("list", help="terse rows only")
    parser.add_argument("--scope")
    parser.add_argument("--lang")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int)
    parser.set_defaults(func=list_verb.cmd_list)


def _add_find_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("find", help="ranked search over the script library")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int)
    parser.set_defaults(func=find.cmd_find)


def _add_run_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser("run", help="execute a script")
    parser.add_argument("name")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--stdin-file", dest="stdin_file")
    # nargs="*" (not REMAINDER): REMAINDER swallows every token after NAME,
    # including flags like --stdin-file if they come after it. "*" respects
    # argparse's own "--" separator, so --timeout/--stdin-file work on
    # either side of NAME, and a literal "--" before trailing script args
    # is consumed rather than showing up inside `args` itself.
    parser.add_argument("args", nargs="*")
    parser.set_defaults(func=run.cmd_run)


def build_parser() -> argparse.ArgumentParser:
    """Top-level `grim` parser: `init` plus the six agent-facing verbs."""
    parser = argparse.ArgumentParser(prog="grim", description="A script-hoarding agent harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create/migrate the grimoire database")
    init_parser.set_defaults(func=cmd_init)
    for add_parser in (
        _add_write_parser,
        _add_update_parser,
        _add_read_parser,
        _add_list_parser,
        _add_find_parser,
        _add_run_parser,
    ):
        add_parser(subparsers)

    assert subparsers.dest == "command", "subparsers must set args.command for dispatch"
    assert parser.prog == "grim", "prog must match the installed console-script name"
    return parser


def _database_ready() -> bool:
    """Whether `grim init` has run — checked once, here, so all six verbs
    fail the same clean way instead of a raw sqlite3.OperationalError."""
    conn = db.connect()
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'script'"
    ).fetchone()
    conn.close()
    return row is not None


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch to the matching cmd_* handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    assert hasattr(args, "func"), "every subcommand must set_defaults(func=...)"
    if args.command != "init" and not _database_ready():
        print("error: database not initialized — run `grim init` first", file=sys.stderr)
        return 1
    result: int = args.func(args)
    assert isinstance(result, int), "cmd_* handlers must return an int exit code"
    return result


if __name__ == "__main__":
    sys.exit(main())
