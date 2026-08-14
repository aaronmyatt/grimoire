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


_SHELL = '''"""shell — escape hatch: run one shell command. Three call shapes:
one arg = a full command line (pipes/redirects work); several args = argv
words rejoined with quoting preserved (a word with spaces stays one word;
shell operators like | are literal in this shape); no args = the command
is read from stdin — the quoting-proof route for multi-line commands.
Usage: shell COMMAND..."""
import shlex
import subprocess
import sys

if len(sys.argv) == 2:
    command = sys.argv[1]
elif len(sys.argv) > 2:
    # shlex.join quotes each word so the shell re-tokenizes to exactly this
    # argv — never the old naive " ".join that dissolved word boundaries.
    # Ref: https://docs.python.org/3/library/shlex.html#shlex.join
    command = shlex.join(sys.argv[1:])
else:
    command = sys.stdin.read()
if not command.strip():
    print("usage: shell COMMAND...  (or pipe the command via stdin)", file=sys.stderr)
    sys.exit(2)
result = subprocess.run(command, shell=True)
sys.exit(result.returncode)
'''

_READ_FILE = '''"""read_file — print a file, optionally sliced by line range.
Usage: read_file PATH [START] [END] (1-indexed, inclusive; END omitted = EOF)
"""
import sys

if not 2 <= len(sys.argv) <= 4:
    print("usage: read_file PATH [START] [END]", file=sys.stderr)
    sys.exit(2)
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

if len(sys.argv) != 2:
    print("usage: write_file PATH  (stdin: content)", file=sys.stderr)
    sys.exit(2)
path = sys.argv[1]
content = sys.stdin.read()
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"wrote {len(content)} bytes to {path}")
'''

_EDIT_FILE = '''"""edit_file — edit a file in place by exact match: replace ONE occurrence
of OLD text with NEW text. Usage: edit_file PATH, with stdin = OLD lines,
a line containing exactly ---GRIM-EDIT---, then NEW lines. Zero or many
matches fail loudly with the count — extend OLD until it is unique."""
import sys

DELIMITER = "---GRIM-EDIT---"

if len(sys.argv) != 2:
    print(f"usage: edit_file PATH  (stdin: OLD, {DELIMITER}, NEW)", file=sys.stderr)
    sys.exit(2)
path = sys.argv[1]
parts = sys.stdin.read().split("\\n" + DELIMITER + "\\n")
if len(parts) != 2:
    print(f"stdin needs exactly one {DELIMITER} line between OLD and NEW", file=sys.stderr)
    sys.exit(1)
old, new = parts
if not old:
    print("OLD text is empty — nothing to match", file=sys.stderr)
    sys.exit(1)
with open(path, encoding="utf-8") as f:
    content = f.read()
count = content.count(old)
if count != 1:
    print(
        f"found {count} matches for OLD in {path} — need exactly 1; "
        "include surrounding lines to make OLD unique",
        file=sys.stderr,
    )
    sys.exit(1)
with open(path, "w", encoding="utf-8") as f:
    f.write(content.replace(old, new, 1))
print(f"edited {path}: replaced 1 occurrence")
'''

_APPLY_PATCH = '''"""apply_patch — apply a unified diff (stdin) via `git apply`, falling
back to `patch -p1` if that fails or git isn't available."""
import subprocess
import sys

if len(sys.argv) != 1:
    print("usage: apply_patch  (stdin: unified diff, no arguments)", file=sys.stderr)
    sys.exit(2)
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

if not 2 <= len(sys.argv) <= 3:
    print("usage: grep_tree PATTERN [PATH]", file=sys.stderr)
    sys.exit(2)
pattern = sys.argv[1]
path = sys.argv[2] if len(sys.argv) > 2 else "."
result = subprocess.run(["rg", "--line-number", "--no-heading", pattern, path])
sys.exit(result.returncode)
'''

_LIST_DIR = '''"""list_dir — structured directory listing (type, size, name).
Usage: list_dir [PATH]"""
import sys
from pathlib import Path

if len(sys.argv) > 2:
    print("usage: list_dir [PATH]", file=sys.stderr)
    sys.exit(2)
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
import sys
from pathlib import Path

if len(sys.argv) != 1:
    print("usage: stats  (no arguments)", file=sys.stderr)
    sys.exit(2)
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
import sys
from pathlib import Path

if len(sys.argv) != 1:
    print("usage: gardener  (no arguments)", file=sys.stderr)
    sys.exit(2)
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

if len(sys.argv) > 2:
    print("usage: export_library [DIR]", file=sys.stderr)
    sys.exit(2)
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "grimoire-export")
db_path = os.environ.get("GRIM_DB") or str(Path.home() / ".grimoire" / "grimoire.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

EXT = {
    "python": "py",
    "bash": "sh",
    "janet": "janet",
    "racket": "rkt",
    "hy": "hy",
    "nim": "nim",
    "ruby": "rb",
    "bun": "ts",
    "php": "php",
    "go": "go",
    "perl": "pl",
    "jq": "jq",
    "sql": "sql",
    "awk": "awk",
    "osascript": "applescript",
    "lua": "lua",
    "luajit": "lua",
    "fennel": "fnl",
    "zig": "zig",
    "duckdb": "sql",
    "prql": "prql",
    "typst": "typ",
    "bc": "bc",
    "dc": "dc",
}

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

_RUN_BG = '''"""run_bg — start a shell command as a detached background job, tagged
grimbg:<name> and logged to <run_dir>/<name>.log. For long-lived processes
(servers, watchers) that a normal `grim run` would kill at its timeout ceiling.
Usage: run_bg NAME COMMAND...  (run dir: $GRIM_RUN_DIR or ~/.grimoire/run)"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

if len(sys.argv) < 3:
    print("usage: run_bg NAME COMMAND...", file=sys.stderr)
    sys.exit(2)

name = sys.argv[1]
# One command arg = a full command line, run verbatim; several = argv words
# rejoined with quoting preserved (same shape rules as the shell seed).
# Ref: https://docs.python.org/3/library/shlex.html#shlex.join
command = sys.argv[2] if len(sys.argv) == 3 else shlex.join(sys.argv[2:])
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
run_dir.mkdir(parents=True, exist_ok=True)
log_path = run_dir / f"{name}.log"
pid_path = run_dir / f"{name}.pid"

# `exec -a` sets argv[0], so `pgrep -f grimbg:` discovers the job by name;
# start_new_session detaches it into its own session/process group so it
# outlives this dispatched script and can be signalled as a group by stop_bg.
tagged = f'exec -a "grimbg:{name}" bash -c {shlex.quote(command)}'
with open(log_path, "ab") as log:
    proc = subprocess.Popen(
        ["bash", "-c", tagged],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
pid_path.write_text(str(proc.pid))
print(f"started grimbg:{name} pid {proc.pid}")
print(f"log: {log_path}")
'''

_LIST_BG = '''"""list_bg — list background jobs started by run_bg: name, pid, state, log.
Reads <run_dir>/*.pid and checks liveness via os.kill(pid, 0)."""
import os
import sys
from pathlib import Path

if len(sys.argv) != 1:
    print("usage: list_bg  (no arguments)", file=sys.stderr)
    sys.exit(2)
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
pid_files = sorted(run_dir.glob("*.pid")) if run_dir.is_dir() else []
if not pid_files:
    print("no background jobs")
    sys.exit(0)

for pid_file in pid_files:
    name = pid_file.stem
    log_path = run_dir / f"{name}.log"
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        print(f"{name}  ?  bad-pid-file")
        continue
    try:
        os.kill(pid, 0)
        state = "running"
    except ProcessLookupError:
        state = "dead"
    except PermissionError:
        state = "running"  # exists but owned by another user
    print(f"{name}  pid {pid}  {state}  log: {log_path}")
'''

_STOP_BG = '''"""stop_bg — stop a background job by name: SIGTERM its process group, then
remove its pid file. Usage: stop_bg NAME"""
import os
import signal
import sys
from pathlib import Path

if len(sys.argv) != 2:
    print("usage: stop_bg NAME", file=sys.stderr)
    sys.exit(2)

name = sys.argv[1]
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
pid_file = run_dir / f"{name}.pid"
if not pid_file.is_file():
    print(f"no such job: {name}", file=sys.stderr)
    sys.exit(1)

pid = int(pid_file.read_text().strip())
try:
    # run_bg used start_new_session, so pid leads its own group; signalling
    # the group takes down any children the job spawned too.
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    print(f"stopped grimbg:{name} (pid {pid})")
except ProcessLookupError:
    print(f"grimbg:{name} already gone (pid {pid})")
finally:
    pid_file.unlink(missing_ok=True)
'''

SEEDS: list[SeedSpec] = [
    SeedSpec(
        name="shell",
        language="python",
        description="run any shell command — the escape hatch when no script fits yet",
        body=_SHELL,
    ),
    SeedSpec(
        name="read_file",
        language="python",
        description="read a file: print its text or source code, whole or by line range",
        body=_READ_FILE,
    ),
    SeedSpec(
        name="write_file",
        language="python",
        description="create or overwrite a file: writes stdin to a path",
        body=_WRITE_FILE,
    ),
    SeedSpec(
        name="edit_file",
        language="python",
        description="edit a file in place: replace one exact match of old text with new text",
        body=_EDIT_FILE,
    ),
    SeedSpec(
        name="apply_patch",
        language="python",
        description="patch, edit or fix files by applying a unified diff (git apply or patch -p1)",
        body=_APPLY_PATCH,
    ),
    SeedSpec(
        name="grep_tree",
        language="python",
        description="search the codebase: find text or a pattern across files (ripgrep)",
        body=_GREP_TREE,
    ),
    SeedSpec(
        name="list_dir",
        language="python",
        description="list files in a directory: type, size and name, for exploring the workspace",
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
    SeedSpec(
        name="run_bg",
        language="python",
        description="start a shell command as a detached background job for long-lived processes",
        body=_RUN_BG,
    ),
    SeedSpec(
        name="list_bg",
        language="python",
        description="list background jobs started by run_bg with their pid and running state",
        body=_LIST_BG,
    ),
    SeedSpec(
        name="stop_bg",
        language="python",
        description="stop a background job started by run_bg by name, killing its process group",
        body=_STOP_BG,
    ),
]
