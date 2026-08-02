"""Global configuration — env-var defaults seeded from ~/.grimoire/config.toml.

Frozen path (root CLAUDE.md §5): part of the shared kernel alongside db.py and
cli.py. `cli.main()` calls `apply_global_config()` once at startup so a value in
~/.grimoire/config.toml becomes the *default* for its env var, while a value
already exported in the shell always wins (os.environ.setdefault). Slices keep
reading plain env vars (GRIM_MODEL, GRIM_DB, GRIM_TIMEOUT, GRIM_TRAJ_DIR) — the
file is just their persistence layer, so no slice has to learn a config format.
"""

from __future__ import annotations

import os
import sys
import tomllib  # stdlib TOML reader, py3.11+  https://docs.python.org/3/library/tomllib.html
from pathlib import Path

# Fixed location (not affected by GRIM_DB, which only moves the database): grim's
# home dir, the same ~/.grimoire db.py resolves DEFAULT_DB_PATH under.
CONFIG_PATH = Path.home() / ".grimoire" / "config.toml"

# config key -> the env var it seeds. Precedence falls out of setdefault:
# shell env > config file > each slice's built-in default. Only these keys are
# recognized; anything else in the file is ignored (forward-compatible).
_CONFIG_ENV_KEYS: dict[str, str] = {
    "model": "GRIM_MODEL",
    "db": "GRIM_DB",
    "timeout": "GRIM_TIMEOUT",
    "traj_dir": "GRIM_TRAJ_DIR",
}


def _load_config(path: Path) -> dict[str, object]:
    """Parse the TOML config, or {} if absent/unreadable/malformed. The file is
    optional, user-authored external input, so a problem here warns and degrades
    to built-in defaults rather than asserting or crashing the CLI (root
    CLAUDE.md §3: VALIDATE external input, don't assert it)."""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[grim] ignoring {path}: {exc}", file=sys.stderr)
        return {}
    assert isinstance(data, dict), "tomllib.loads returns a table (dict)"
    return data


def apply_global_config(path: Path | None = None) -> None:
    """Seed env-var defaults from the config file. A shell-set var is never
    overwritten (os.environ.setdefault) — precedence is env > file > default.
    Idempotent: safe to call on every cli.main() entry, including the adapter's
    repeated in-process calls within one agent run."""
    cfg_path = path if path is not None else CONFIG_PATH
    assert cfg_path is not None, "config path resolves to a value"
    data = _load_config(cfg_path)
    applied = 0
    for key, env in _CONFIG_ENV_KEYS.items():
        value = data.get(key)
        # bool is an int subclass; a `model = true` is nonsense, so exclude it.
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            os.environ.setdefault(env, str(value))
            applied += 1
    assert applied <= len(_CONFIG_ENV_KEYS), "cannot apply more keys than are known"
