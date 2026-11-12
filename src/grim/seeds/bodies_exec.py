"""Seed bodies that start other programs — no shell anywhere (the python-only
pivot, docs/python-pivot-plan.md). Data, not code: each constant is one
seed's full script text; bodies.py's SEEDS roster wires them in.

Every body here takes a program and its arguments as argv and execs it
directly (`shell=False`): no pipes, redirects, `&&`, globbing, or `cd`, and
a local shell interpreter as the program is refused — that work belongs in
a python script. Ref: https://docs.python.org/3/library/subprocess.html#security-considerations
"""

from __future__ import annotations

RUN_CMD = '''"""run_cmd — run ONE program with its arguments, no shell. Examples:
run_cmd git status   run_cmd uv run pytest -q   run_cmd curl -sS URL
A single quoted command string is split shlex-style. Pipes, redirects, &&,
;, globbing, cd, and shell interpreters (bash, sh, ...) are refused — chain
and filter in python instead (run_py for a one-off, a named script if you'll
do it twice). Exit code, stdin and output pass straight through.
Usage: run_cmd PROGRAM [ARG...]
Ref: https://docs.python.org/3/library/subprocess.html#subprocess.run"""
import os
import shlex
import shutil
import subprocess
import sys

SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>&1", "&>"}
SHELLS = {"bash", "sh", "zsh", "dash", "fish", "ksh", "csh", "tcsh"}
# Programs a seed already covers with bounded, structured output. The hint
# goes to stderr and the command still runs: a nudge, never a new failure.
BETTER_SEEDS = {
    "cat": "read_file PATH [START] [END]",
    "head": "read_file PATH 1 N",
    "tail": "read_file PATH START",
    "sed": "read_file PATH START END to view, edit_file to edit",
    "grep": "grep_tree PATTERN [PATH]",
    "rg": "grep_tree PATTERN [PATH]",
    "ls": "list_dir [PATH]",
    "find": "list_dir [PATH] or grep_tree PATTERN [PATH]",
}

if len(sys.argv) < 2:
    print("usage: run_cmd PROGRAM [ARG...]", file=sys.stderr)
    sys.exit(2)
argv = shlex.split(sys.argv[1]) if len(sys.argv) == 2 else sys.argv[1:]
operators = sorted({token for token in argv if token in SHELL_OPERATORS})
if not argv or operators or os.path.basename(argv[0]) in SHELLS:
    print(
        f"run_cmd: no shell here (got {operators or argv[:1]}). Run one program, and do "
        "the chaining/filtering in python: run_py for a one-off, or write a named script.",
        file=sys.stderr,
    )
    sys.exit(2)
program = os.path.basename(argv[0])
if program in BETTER_SEEDS:
    print(f"[run_cmd] hint: the {BETTER_SEEDS[program]} seed does this better", file=sys.stderr)
if shutil.which(argv[0]) is None:
    # The 'command not found' class: usually a library script called as if it
    # were a program on PATH. Library scripts are reached through run(name=...).
    print(
        f"run_cmd: {argv[0]!r} is not a program on PATH. If it is a library script, "
        f'call run(name="{argv[0]}") instead.',
        file=sys.stderr,
    )
    sys.exit(127)
sys.exit(subprocess.run(argv, check=False).returncode)
'''

RUN_PY = '''"""run_py — run a one-off python snippet, read from stdin, as __main__;
any ARGs become sys.argv[1:]. The quick path for chaining and filtering that
used to be a shell pipeline (subprocess.run for programs, pathlib/re/json for
the rest). The snippet IS stdin, so it reads data from files, not stdin.
Stdlib only: for third-party packages write a named script with a PEP 723
`# /// script` header. If you'd run it twice, make it a named script.
Usage: run_py [ARG...]   (snippet on stdin)
Ref: https://docs.python.org/3/library/functions.html#exec"""
import sys

source = sys.stdin.read()
if not source.strip():
    print("usage: run_py [ARG...]  (python snippet on stdin)", file=sys.stderr)
    sys.exit(2)
sys.argv = ["run_py", *sys.argv[1:]]
code = compile(source, "<run_py>", "exec")
exec(code, {"__name__": "__main__", "__builtins__": __builtins__})
'''
