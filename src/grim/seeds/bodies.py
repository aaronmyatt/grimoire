"""Seed script bodies (build plan §3 Phase 3, D11) — data, not code this
project's own toolchain holds to grim's code-quality bar. Read by
loader.py; nothing else should import from here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedSpec:
    name: str
    language: str
    description: str
    body: str


_SHELL = '''"""shell — escape hatch: run one shell command. Usage: shell COMMAND..."""
import subprocess
import sys

command = " ".join(sys.argv[1:])
result = subprocess.run(command, shell=True)
sys.exit(result.returncode)
'''

_READ_FILE = '''"""read_file — print a file, optionally sliced by line range.
Usage: read_file PATH [START] [END] (1-indexed, inclusive; END omitted = EOF)
"""
import sys

path = sys.argv[1]
start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
end = int(sys.argv[3]) if len(sys.argv) > 3 else len(lines)
for i, line in enumerate(lines[start - 1 : end], start=start):
    print(f"{i}\\t{line}", end="" if line.endswith("\\n") else "\\n")
'''

_WRITE_FILE = '''"""write_file — write stdin to a file path. Usage: write_file PATH"""
import sys

path = sys.argv[1]
content = sys.stdin.read()
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"wrote {len(content)} bytes to {path}")
'''

_APPLY_PATCH = '''"""apply_patch — apply a unified diff (stdin) via `git apply`, falling
back to `patch -p1` if that fails or git isn't available."""
import subprocess
import sys

patch_text = sys.stdin.read()

result = subprocess.run(
    ["git", "apply", "--whitespace=nowarn", "-"], input=patch_text, text=True, capture_output=True
)
if result.returncode == 0:
    print("applied via git apply")
    sys.exit(0)

fallback = subprocess.run(["patch", "-p1"], input=patch_text, text=True, capture_output=True)
if fallback.returncode == 0:
    print("applied via patch -p1 (git apply failed)")
    sys.exit(0)

print(f"git apply failed:\\n{result.stderr}", file=sys.stderr)
print(f"patch -p1 also failed:\\n{fallback.stderr}", file=sys.stderr)
sys.exit(1)
'''

_GREP_TREE = '''"""grep_tree — ripgrep wrapper with sane defaults (line numbers, respects
.gitignore). Usage: grep_tree PATTERN [PATH]"""
import subprocess
import sys

pattern = sys.argv[1]
path = sys.argv[2] if len(sys.argv) > 2 else "."
result = subprocess.run(["rg", "--line-number", "--no-heading", pattern, path])
sys.exit(result.returncode)
'''

_LIST_DIR = '''"""list_dir — structured directory listing (type, size, name).
Usage: list_dir [PATH]"""
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
for entry in sorted(path.iterdir(), key=lambda p: p.name):
    kind = "dir" if entry.is_dir() else "file"
    size = entry.stat().st_size if entry.is_file() else "-"
    print(f"{kind}\\t{size}\\t{entry.name}")
'''

_STATS = '''"""stats — usage report: total runs, shell-escape rate, reuse rate,
active library % (build plan §7). Opens its own sqlite connection —
this runs as an isolated subprocess with no access to grim's
in-process connection, same as every script (D8)."""
import os
import sqlite3
from pathlib import Path

db_path = os.environ.get("GRIM_DB") or str(Path.home() / ".grimoire" / "grimoire.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

total_runs = conn.execute("SELECT COUNT(*) AS n FROM execution").fetchone()["n"]

shell_runs = conn.execute(
    "SELECT COUNT(*) AS n FROM execution e "
    "JOIN script_version sv ON sv.id = e.script_version_id "
    "JOIN script s ON s.id = sv.script_id WHERE s.name = 'shell'"
).fetchone()["n"]

reused_runs = conn.execute(
    "SELECT COUNT(*) AS n FROM execution e "
    "JOIN script_version sv ON sv.id = e.script_version_id "
    "JOIN script s ON s.id = sv.script_id "
    "WHERE s.origin_session_id IS NOT NULL AND s.origin_session_id != e.session_id"
).fetchone()["n"]

active_scripts = conn.execute(
    "SELECT COUNT(DISTINCT s.id) AS n FROM script s "
    "JOIN script_version sv ON sv.script_id = s.id "
    "JOIN execution e ON e.script_version_id = sv.id "
    "WHERE s.archived = 0 AND e.started_at >= datetime('now', '-30 days')"
).fetchone()["n"]

total_scripts = conn.execute("SELECT COUNT(*) AS n FROM script WHERE archived = 0").fetchone()["n"]


def pct(numerator, denominator):
    return f"{numerator / denominator:.2%}" if denominator else "n/a"


print(f"total runs: {total_runs}")
print(f"shell-escape rate: {pct(shell_runs, total_runs)}")
print(f"reuse rate: {pct(reused_runs, total_runs)}")
print(f"active library: {pct(active_scripts, total_scripts)}")
'''

_GARDENER = '''"""gardener — dup/stale sweep. Reports candidates for archiving; never
archives anything itself (a human reviews and acts separately)."""
import os
import sqlite3
from pathlib import Path

db_path = os.environ.get("GRIM_DB") or str(Path.home() / ".grimoire" / "grimoire.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== exact duplicates (same body as another script's latest version) ===")
dupes = conn.execute(
    "SELECT sv.body_hash, GROUP_CONCAT(s.name, ', ') AS names FROM script_version sv "
    "JOIN script s ON s.id = sv.script_id "
    "WHERE sv.version = ("
    "  SELECT MAX(v2.version) FROM script_version v2 WHERE v2.script_id = sv.script_id"
    ") AND s.archived = 0 "
    "GROUP BY sv.body_hash HAVING COUNT(DISTINCT s.id) > 1"
).fetchall()
if not dupes:
    print("  (none)")
for row in dupes:
    print(f"  {row['names']}")

print("=== stale (not seeded, no runs in last 30 days) ===")
stale = conn.execute(
    "SELECT s.name FROM script s LEFT JOIN script_health h ON h.id = s.id "
    "WHERE s.archived = 0 AND s.seeded = 0 "
    "AND (h.last_used IS NULL OR h.last_used < datetime('now', '-30 days')) "
    "ORDER BY s.name"
).fetchall()
if not stale:
    print("  (none)")
for row in stale:
    print(f"  {row['name']}")
'''

_EXPORT_LIBRARY = '''"""export_library — dump the latest version of every non-archived
script to a git-friendly directory tree. Usage: export_library [DIR]"""
import os
import sqlite3
import sys
from pathlib import Path

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "grimoire-export")
db_path = os.environ.get("GRIM_DB") or str(Path.home() / ".grimoire" / "grimoire.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

EXT = {"python": "py", "bash": "sh"}

rows = conn.execute(
    "SELECT s.name, s.language, sv.body FROM script s "
    "JOIN script_version sv ON sv.script_id = s.id "
    "WHERE s.archived = 0 AND sv.version = ("
    "  SELECT MAX(v2.version) FROM script_version v2 WHERE v2.script_id = s.id"
    ")"
).fetchall()

out_dir.mkdir(parents=True, exist_ok=True)
for row in rows:
    ext = EXT.get(row["language"], "txt")
    (out_dir / f"{row['name']}.{ext}").write_text(row["body"])

print(f"exported {len(rows)} scripts to {out_dir}/")
'''

SEEDS: list[SeedSpec] = [
    SeedSpec(
        name="shell",
        language="python",
        description="escape hatch: run one shell command passed as argv",
        body=_SHELL,
    ),
    SeedSpec(
        name="read_file",
        language="python",
        description="print a file's contents, optionally sliced by line range",
        body=_READ_FILE,
    ),
    SeedSpec(
        name="write_file",
        language="python",
        description="write stdin to a file path, overwriting it",
        body=_WRITE_FILE,
    ),
    SeedSpec(
        name="apply_patch",
        language="python",
        description="apply a unified diff via git apply, falling back to patch -p1",
        body=_APPLY_PATCH,
    ),
    SeedSpec(
        name="grep_tree",
        language="python",
        description="ripgrep wrapper with sane defaults for searching a directory tree",
        body=_GREP_TREE,
    ),
    SeedSpec(
        name="list_dir",
        language="python",
        description="structured directory listing: type, size, name",
        body=_LIST_DIR,
    ),
    SeedSpec(
        name="stats",
        language="python",
        description="usage report: total runs, shell-escape rate, reuse rate, active library %",
        body=_STATS,
    ),
    SeedSpec(
        name="gardener",
        language="python",
        description="dup/stale sweep proposing archive candidates, never archives itself",
        body=_GARDENER,
    ),
    SeedSpec(
        name="export_library",
        language="python",
        description="dump the latest version of every non-archived script to a directory tree",
        body=_EXPORT_LIBRARY,
    ),
]
