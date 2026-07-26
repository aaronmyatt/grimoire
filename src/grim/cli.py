"""`grim` command-line entry point — argparse dispatch to cmd_* handlers.

Frozen path (root CLAUDE.md §5): human-confirmed on every write, its own
commit. Phase 0 wires only `init`; Phase 1 adds the six agent-facing verbs
(write/update/read/list/find/run) as its own task, each dispatched from
here but implemented in src/grim/verbs/ (build plan §4, §12/D12).
"""

from __future__ import annotations

import argparse
import sys

from grim import db


def cmd_init(args: argparse.Namespace) -> int:
    """`grim init`: connect + migrate, reporting what was newly applied."""
    conn = db.connect()
    applied = db.migrate(conn)
    print(f"applied: {', '.join(applied)}" if applied else "already up to date")
    assert isinstance(applied, list), "migrate() must always return a list"
    assert conn is not None, "connect() must always return a connection or raise"
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Top-level `grim` parser. Phase 1 adds subparsers for the six verbs."""
    parser = argparse.ArgumentParser(prog="grim", description="A script-hoarding agent harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create/migrate the grimoire database")
    init_parser.set_defaults(func=cmd_init)

    assert subparsers.dest == "command", "subparsers must set args.command for dispatch"
    assert parser.prog == "grim", "prog must match the installed console-script name"
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch to the matching cmd_* handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    assert hasattr(args, "func"), "every subcommand must set_defaults(func=...)"
    result: int = args.func(args)
    assert isinstance(result, int), "cmd_* handlers must return an int exit code"
    return result


if __name__ == "__main__":
    sys.exit(main())
