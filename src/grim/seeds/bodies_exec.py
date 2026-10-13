"""Seed bodies that start other programs — no shell anywhere (the python-only
pivot, docs/python-pivot-plan.md). Data, not code: each constant is one
seed's full script text; bodies.py's SEEDS roster wires them in.

Every body here takes a program and its arguments as argv and execs it
directly (`shell=False`): no pipes, redirects, `&&`, globbing, or `cd`, and
a local shell interpreter as the program is refused — that work belongs in
a python script. Ref: https://docs.python.org/3/library/subprocess.html#security-considerations
"""

from __future__ import annotations

RUN_BG = '''"""run_bg — start a program as a detached background job, logged to
<run_dir>/<name>.log: for long-lived processes (servers, watchers) a normal
`grim run` would kill at its timeout. No shell: one program plus its args.
Usage: run_bg NAME PROGRAM [ARG...]   (a single quoted command string is
split shlex-style). Pipes, redirects, && and ; are refused, as are shell
interpreters (bash, sh, ...) — put that logic in a python script and
background it: run_bg NAME grim run SCRIPT. Run dir: $GRIM_RUN_DIR or
~/.grimoire/run. Ref: https://docs.python.org/3/library/shlex.html#shlex.split"""
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>&1", "&>"}
SHELLS = {"bash", "sh", "zsh", "dash", "fish", "ksh", "csh", "tcsh"}

if len(sys.argv) < 3:
    print("usage: run_bg NAME PROGRAM [ARG...]", file=sys.stderr)
    sys.exit(2)
name = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
    print(f"run_bg: bad job name {name!r} (letters, digits, _ . - only)", file=sys.stderr)
    sys.exit(2)
argv = shlex.split(sys.argv[2]) if len(sys.argv) == 3 else sys.argv[2:]
operators = sorted({token for token in argv if token in SHELL_OPERATORS})
if not argv or operators or os.path.basename(argv[0]) in SHELLS:
    print(
        f"run_bg: no shell here (got {operators or argv[:1]}). Background one program, "
        "or write a python script and run: run_bg NAME grim run SCRIPT",
        file=sys.stderr,
    )
    sys.exit(2)

run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
run_dir.mkdir(parents=True, exist_ok=True)
log_path = run_dir / f"{name}.log"
pid_path = run_dir / f"{name}.pid"

# start_new_session detaches the job into its own session/process group so it
# outlives this script and stop_bg can signal the whole group. The pid file is
# the job registry list_bg/stop_bg read; GRIMBG_NAME tags the job's own env.
with open(log_path, "ab") as log:
    try:
        proc = subprocess.Popen(
            argv,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "GRIMBG_NAME": name},
        )
    except FileNotFoundError:
        print(f"run_bg: program not found on PATH: {argv[0]!r}", file=sys.stderr)
        sys.exit(127)
pid_path.write_text(str(proc.pid))
print(f"started grimbg:{name} pid {proc.pid}")
print(f"log: {log_path}")
'''

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
