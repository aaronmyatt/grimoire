"""SQLite connection, PRAGMAs, and migration runner — the shared kernel.

Frozen path (root CLAUDE.md §5): every write here is human-confirmed, and
changes land as their own reviewed commit, never as a side effect of slice
work. See build plan §3 for the schema this migrates towards and D3/D4 for
why SQLite + WAL + append-only versioning were chosen.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".grimoire" / "grimoire.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def resolve_db_path() -> Path:
    """Return the GRIM_DB override or DEFAULT_DB_PATH, creating its parent dir.

    GRIM_DB lets tests and future `--check` tooling point at a scratch file
    instead of the real library (build plan D3: "GRIM_DB override").
    """
    override = os.environ.get("GRIM_DB")
    path = (Path(override) if override else DEFAULT_DB_PATH).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert path.is_absolute(), "resolved db path must be absolute"
    assert path.parent.is_dir(), "db parent directory must exist after mkdir"
    return path


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with the PRAGMAs build plan D3/D4 depend on.

    WAL mode (https://sqlite.org/wal.html) lets one writer and readers
    overlap without blocking — the concurrency story build plan Phase 4
    settles on for "one agent + one human." foreign_keys defaults OFF in
    SQLite for backward compatibility and must be set per-connection
    (https://sqlite.org/pragma.html#pragma_foreign_keys).
    """
    path = db_path if db_path is not None else resolve_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert str(mode).lower() == "wal", f"expected WAL journal mode, got {mode}"
    assert fk == 1, "foreign_keys PRAGMA did not take effect"
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply migrations/*.sql not yet recorded, in filename order.

    Idempotent: re-running against an up-to-date database applies nothing
    and returns an empty list (build plan Phase 0 "done when": fresh init
    and re-init both succeed).
    """
    assert MIGRATIONS_DIR.is_dir(), f"missing migrations dir: {MIGRATIONS_DIR}"
    applied = _applied_versions(conn)
    pending = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
    newly_applied: list[str] = []
    for name in pending:
        sql = (MIGRATIONS_DIR / name).read_text()
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (name,))
        conn.commit()
        newly_applied.append(name)
    assert set(newly_applied).isdisjoint(applied), "must never re-apply a recorded migration"
    return newly_applied


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """connect() + migrate() — a ready-to-use connection for callers who
    don't need the list of newly-applied migrations (that's `migrate`'s
    direct return value; `grim init` calls connect+migrate itself so it
    can report what it applied)."""
    conn = connect(db_path)
    migrate(conn)
    assert conn is not None, "connect() must always return a connection or raise"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "post-migrate PRAGMA must hold"
    return conn
