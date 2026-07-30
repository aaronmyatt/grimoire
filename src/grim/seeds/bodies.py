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
]
