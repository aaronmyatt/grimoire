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
]
