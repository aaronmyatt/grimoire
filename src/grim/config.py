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
from dataclasses import dataclass
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
    "run_dir": "GRIM_RUN_DIR",  # background-job dir (run_bg/list_bg/stop_bg seeds)
    "cost": "GRIM_COST_LIMIT",  # mini agent limits — consumed by the launcher, not verbs
    "step": "GRIM_STEP_LIMIT",
    "changelog_model": "GRIM_CHANGELOG_MODEL",  # `grim edit`'s AI changelog; falls back to model
}

# Bound the upward search for a repo-local config (CLAUDE.md §3: every loop has
# an explicit max) — deep enough to reach a repo root from any real subdir.
_MAX_PARENTS = 64


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


def _repo_config_path() -> Path | None:
    """Nearest ./.grimoire/config.toml walking up from cwd (like git's .git
    discovery), or None. Excludes the global config itself so a cwd under $HOME
    doesn't rediscover ~/.grimoire/config.toml as a repo config. Bounded climb —
    never an unbounded walk up the tree."""
    assert _MAX_PARENTS > 0, "the upward search must be bounded and positive"
    global_resolved = CONFIG_PATH.resolve()
    current = Path.cwd().resolve()
    for _ in range(_MAX_PARENTS):
        candidate = current / ".grimoire" / "config.toml"
        if candidate.is_file() and candidate != global_resolved:
            return candidate
        if current.parent == current:  # reached the filesystem root
            return None
        current = current.parent
    return None


def _apply_config(data: dict[str, object]) -> None:
    """setdefault each known key's env var from a parsed config table. A
    shell-set var is never overwritten, and a key already seeded by a
    higher-priority file wins over a lower one — both fall out of setdefault."""
    assert isinstance(data, dict), "config data is a parsed table"
    applied = 0
    for key, env in _CONFIG_ENV_KEYS.items():
        value = data.get(key)
        # bool is an int subclass; a `model = true` is nonsense, so exclude it.
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            os.environ.setdefault(env, str(value))
            applied += 1
    assert applied <= len(_CONFIG_ENV_KEYS), "cannot apply more keys than are known"


def apply_global_config(path: Path | None = None) -> None:
    """Seed env-var defaults from the config files. Precedence, all falling out
    of os.environ.setdefault (first-set-wins): shell env > repo config (nearest
    ./.grimoire/config.toml) > global (~/.grimoire/config.toml) > built-in
    default. `path` overrides the GLOBAL location (tests/tooling). Idempotent —
    safe to call on every cli.main() entry, including the adapter's repeated
    in-process calls within one agent run."""
    global_path = path if path is not None else CONFIG_PATH
    assert global_path is not None, "global config path resolves to a value"
    repo_path = _repo_config_path()
    if repo_path is not None:  # applied first, so repo beats global under setdefault
        _apply_config(_load_config(repo_path))
    _apply_config(_load_config(global_path))


@dataclass(frozen=True)
class Setting:
    key: str  # the config.toml key
    env: str  # the env var it seeds
    value: str | None  # effective value now (None = unset -> a slice's built-in default)
    source: str  # "env" | "repo" | "global" | "default"


def _classify(value: str | None, repo_val: object, global_val: object) -> str:
    """Infer a setting's source by precedence (env > repo > global > default).
    A shell value that coincidentally equals a file's is reported as that file
    — a harmless approximation for a read-only diagnostic."""
    assert value is None or isinstance(value, str), "env value is a str or None"
    if value is None:
        return "default"
    if repo_val is not None and value == str(repo_val):
        return "repo"
    if global_val is not None and value == str(global_val):
        return "global"
    return "env"


def effective_config() -> list[Setting]:
    """Resolve every known key to its effective value + source, for `grim
    config`. Pure over os.environ + the two config files — needs neither the DB
    nor apply_global_config to have run, so it works when things are broken."""
    repo_path = _repo_config_path()
    repo = _load_config(repo_path) if repo_path is not None else {}
    global_data = _load_config(CONFIG_PATH)
    settings: list[Setting] = []
    for key, env in _CONFIG_ENV_KEYS.items():
        value = os.environ.get(env)
        source = _classify(value, repo.get(key), global_data.get(key))
        settings.append(Setting(key=key, env=env, value=value, source=source))
    assert len(settings) == len(_CONFIG_ENV_KEYS), "one setting per known key"
    assert all(isinstance(s, Setting) for s in settings), "settings are Setting rows"
    return settings
