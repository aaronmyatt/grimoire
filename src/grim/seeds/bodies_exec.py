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
