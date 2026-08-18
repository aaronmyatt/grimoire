"""`grim` command-line entry point — argparse dispatch to cmd_* handlers.

Frozen path (root CLAUDE.md §5): human-confirmed on every write, its own
commit. Wires the six agent-facing verbs (build plan §4, §12/D12) to their
implementations in src/grim/verbs/ — this module owns argument shapes
only, never verb logic.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grim import completions, config, db
from grim.curate import edit, near, recent, tags
from grim.exec import dispatch
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
    try:
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
        # Shell completion is part of setup, not a hard requirement: a broken
        # HOME or missing shell must never fail `grim init`.
        try:
            completions.install()
        except Exception as exc:
            print(f"warning: could not install shell completion: {exc}", file=sys.stderr)
        return 0
    finally:
        conn.close()


def cmd_config(args: argparse.Namespace) -> int:
    """`grim config`: print each known setting, its effective value, and its
    source (env / repo / global / default) — like `git config --list`. Reads
    files + env only, so it works even before `grim init` (human-only, D12)."""
    settings = config.effective_config()
    for setting in settings:
        value = setting.value if setting.value is not None else "(unset)"
        print(f"{setting.key:<9} {setting.env:<16} {value:<42} ({setting.source})")
    assert settings, "there is always at least one known setting"
    assert all(s.env for s in settings), "every setting names an env var"
    return 0


@dataclass
class _Check:
    label: str
    ok: bool
    detail: str
    critical: bool = True  # a failed critical check makes `grim doctor` exit nonzero


# uv/bash are required (python + bash dispatch, the shell seed); rg/git back
# specific seeds (grep_tree, apply_patch — git has a patch fallback), so their
# absence is a warning, not a hard failure.
_REQUIRED_TOOLS = ("uv", "bash")
_OPTIONAL_TOOLS = ("rg", "git")


def _tool_check(
    tool: str, *, critical: bool, hint: str | None = None, label: str | None = None
) -> _Check:
    path = shutil.which(tool)
    hint = hint or ("required" if critical else "a seed needs it")
    detail = path or f"not on PATH — {hint}"
    return _Check(label or f"tool: {tool}", path is not None, detail, critical)


def _fts5_check() -> _Check:
    """FTS5 backs `find`; probe a throwaway in-memory table rather than assume
    the SQLite build has it compiled in."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        return _Check("sqlite: fts5", True, "available")
    except sqlite3.OperationalError as exc:
        return _Check("sqlite: fts5", False, f"unavailable: {exc}")
    finally:
        conn.close()


def _db_check() -> _Check:
    ready = _database_ready()
    detail = f"{db.resolve_db_path()} ({'migrated' if ready else 'run `grim init`'})"
    return _Check("database", ready, detail, critical=False)


def _config_check() -> _Check:
    path = config.CONFIG_PATH
    if not path.is_file():
        return _Check("config", True, "no global config (using defaults)", critical=False)
    try:
        tomllib.loads(path.read_text())
        return _Check("config", True, f"{path} parses", critical=False)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return _Check("config", False, f"{path}: {exc}", critical=False)


def _language_checks() -> list[_Check]:
    """One warning-level check per language the user enabled in config:
    interpreter availability for platform-valid ones, a why-not note for the
    rest (unknown name, wrong OS). Never critical — opt-ins are off by
    default, and doctor must not fail because one is missing."""
    checks: list[_Check] = []
    for lang in sorted(dispatch.requested_languages()):
        if lang in dispatch.enabled_languages():
            tool = dispatch.language_tool(lang)
            checks.append(
                _tool_check(
                    tool,
                    critical=False,
                    hint="enabled in config [languages]",
                    label=f"lang: {tool}",
                )
            )
        else:
            detail = dispatch.language_status(lang)
            checks.append(_Check(f"lang: {lang}", True, detail, critical=False))
    assert len(checks) <= len(dispatch.requested_languages()), "one check per requested language"
    return checks


def _doctor_checks() -> list[_Check]:
    checks = [_tool_check(t, critical=True) for t in _REQUIRED_TOOLS]
    checks += [_tool_check(t, critical=False) for t in _OPTIONAL_TOOLS]
    checks += _language_checks()
    checks += [_fts5_check(), _db_check(), _config_check()]
    assert len(checks) >= len(_REQUIRED_TOOLS), "doctor runs at least the required-tool checks"
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    """`grim doctor`: verify the runtime substrate — required tools (uv/bash),
    optional seed tools (rg/git), FTS5, DB state, config parse — in-process, so
    it works even when dispatch or the DB is broken (human-only, D12). Exits
    nonzero only if a *critical* check fails."""
    failed = 0
    for check in _doctor_checks():
        status = "ok" if check.ok else ("FAIL" if check.critical else "warn")
        failed += 1 if (not check.ok and check.critical) else 0
        print(f"[{status:>4}] {check.label:<14} {check.detail}")
    assert failed >= 0, "failure count is non-negative"
    return 1 if failed else 0


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
    # Optional, unlike write's required --lang: omitted means "same language as
    # the current version", which is the overwhelmingly common case. Supplying
    # it rewrites the script in a different language instead of forcing a
    # near-duplicate fork.
    parser.add_argument("--lang")
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
    # choices gate the value so verbs/list.py can interpolate it into ORDER BY
    # safely (see _SORT_CLAUSES there); default keeps stable alphabetical order.
    parser.add_argument("--sort", choices=("name", "recent", "runs"), default="name")
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
    # --head/--tail: opt into first-N/last-M line limiting of the run
    # observation. Omitted (both None) means full output — grim run shows
    # the script's complete stdout/stderr by default; these are the escape
    # hatch for the occasional huge-output script (the full text is stored
    # regardless and pageable via `grim read --exec <id>`).
    parser.add_argument("--head", type=int, dest="head")
    parser.add_argument("--tail", type=int, dest="tail")
    # nargs="*" (not REMAINDER): REMAINDER swallows every token after NAME,
    # including flags like --stdin-file if they come after it. "*" respects
    # argparse's own "--" separator, so --timeout/--stdin-file work on
    # either side of NAME, and a literal "--" before trailing script args
    # is consumed rather than showing up inside `args` itself.
    parser.add_argument("args", nargs="*")
    parser.set_defaults(func=run.cmd_run)


def _add_completion_parser(subparsers: _SubParsers) -> None:
    parser = subparsers.add_parser(
        "completion", help="install/uninstall bash+zsh completion for grim"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--uninstall", action="store_true", help="remove completion files and rc hooks"
    )
    group.add_argument("--print-bash", action="store_true", help="print the bash completion source")
    group.add_argument("--print-zsh", action="store_true", help="print the zsh completion source")
    group.add_argument("--check", action="store_true", help="report completion install state")
    group.add_argument(
        "--selftest",
        action="store_true",
        help="syntax-check both snippets and verify the db query",
    )
    parser.set_defaults(func=completions.cmd_completion)


def build_parser() -> argparse.ArgumentParser:
    """Top-level `grim` parser: `init` plus the six agent-facing verbs."""
    parser = argparse.ArgumentParser(prog="grim", description="A script-hoarding agent harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create/migrate the grimoire database")
    init_parser.set_defaults(func=cmd_init)
    config_parser = subparsers.add_parser("config", help="show effective settings and their source")
    config_parser.set_defaults(func=cmd_config)
    doctor_parser = subparsers.add_parser("doctor", help="check tools, FTS5, database, and config")
    doctor_parser.set_defaults(func=cmd_doctor)
    # Human-only browse commands (curate slice) — dispatched here but never
    # added to adapter/tools.py::GRIM_TOOLS, so they stay off the agent surface.
    near_parser = subparsers.add_parser("near", help="scripts that run adjacently to NAME")
    near_parser.add_argument("name")
    near_parser.add_argument("--limit", type=int)
    near_parser.set_defaults(func=near.cmd_near)
    recent_parser = subparsers.add_parser("recent", help="library by last-run time, newest first")
    recent_parser.add_argument("--limit", type=int)
    recent_parser.set_defaults(func=recent.cmd_recent)
    edit_parser = subparsers.add_parser("edit", help="round-trip a script through $EDITOR")
    edit_parser.add_argument("name")
    edit_parser.add_argument("--changelog", help="skip AI/manual changelog prompts")
    edit_parser.set_defaults(func=edit.cmd_edit)
    tag_parser = subparsers.add_parser("tag", help="attach tags to a script")
    tag_parser.add_argument("name")
    tag_parser.add_argument("tags", nargs="+")
    tag_parser.set_defaults(func=tags.cmd_tag)
    untag_parser = subparsers.add_parser("untag", help="remove tags from a script")
    untag_parser.add_argument("name")
    untag_parser.add_argument("tags", nargs="+")
    untag_parser.set_defaults(func=tags.cmd_untag)
    tags_parser = subparsers.add_parser("tags", help="list all tags with usage counts")
    tags_parser.add_argument("--limit", type=int)
    tags_parser.set_defaults(func=tags.cmd_tags)
    tagged_parser = subparsers.add_parser("tagged", help="scripts carrying TAG")
    tagged_parser.add_argument("tag")
    tagged_parser.add_argument("--limit", type=int)
    tagged_parser.set_defaults(func=tags.cmd_tagged)
    # Sugar over tag/untag/tagged with the well-known "favourite" tag — not
    # a separate schema column (curate/CLAUDE.md).
    favourite_parser = subparsers.add_parser("favourite", help="star a script")
    favourite_parser.add_argument("name")
    favourite_parser.set_defaults(func=tags.cmd_favourite)
    unfavourite_parser = subparsers.add_parser("unfavourite", help="unstar a script")
    unfavourite_parser.add_argument("name")
    unfavourite_parser.set_defaults(func=tags.cmd_unfavourite)
    favourites_parser = subparsers.add_parser("favourites", help="list starred scripts")
    favourites_parser.add_argument("--limit", type=int)
    favourites_parser.set_defaults(func=tags.cmd_favourites)
    for add_parser in (
        _add_write_parser,
        _add_update_parser,
        _add_read_parser,
        _add_list_parser,
        _add_find_parser,
        _add_run_parser,
        _add_completion_parser,
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
    # Seed env-var defaults from ~/.grimoire/config.toml before any handler
    # reads them (a shell-set var still wins). This is the single entry point
    # for both the human CLI and the agent (adapter -> cli.main), so config
    # applies everywhere without any slice or the launcher knowing about it.
    config.apply_global_config()
    parser = build_parser()
    args = parser.parse_args(argv)
    assert hasattr(args, "func"), "every subcommand must set_defaults(func=...)"
    # init creates the DB; config and doctor are diagnostics that must work
    # before (and when) the library is broken, so none is gated on a ready DB.
    if args.command not in ("init", "config", "doctor", "completion") and not _database_ready():
        print("error: database not initialized — run `grim init` first", file=sys.stderr)
        return 1
    result: int = args.func(args)
    assert isinstance(result, int), "cmd_* handlers must return an int exit code"
    return result


if __name__ == "__main__":
    sys.exit(main())
