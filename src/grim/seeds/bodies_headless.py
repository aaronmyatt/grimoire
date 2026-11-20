"""Seed bodies for headless delegates (docs/delegation-plan.md, Phase 2); bodies.py's
SEEDS wires HEADLESS_SEEDS in. _SHARED is shared like bodies_web_go.py's _GO_SHARED."""

from __future__ import annotations

# fail/powers/program_for; the body sets NAME, AGENTS, imports json os shutil sys tomllib Path.
_SHARED = r'''

def fail(code, message):
    print(f"{NAME}: {message}", file=sys.stderr)
    sys.exit(code)


def powers(text, agents):
    """--exec-args JSON as {agent: [flag, ...]}. Every agent named must have its
    list: a headless delegate never runs on powers nobody wrote down."""
    assert agents and set(agents) <= set(AGENTS), "only the headless agents"
    try:
        stated = json.loads(text)
    except ValueError:
        stated = None
    for agent in agents:
        flags = stated.get(agent) if isinstance(stated, dict) else None
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            fail(2, f"powers must be stated: --exec-args '{{\"{agent}\": [\"--flag\", ...]}}'")
    assert isinstance(stated, dict), "every agent's powers were checked above"
    return stated


def program_for(agent):
    """[delegates.AGENT] bin in the config file if set, else the first AGENT on
    PATH that is not a tmux-launching wrapper (it would run in another window)."""
    path = Path(os.environ.get("GRIM_DELEGATES_CONFIG") or Path.home() / ".grimoire/config.toml")
    try:
        loaded = tomllib.loads(path.read_text()) if path.is_file() else {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        fail(1, f"cannot read {path}: {exc}")
    configured = loaded.get("delegates", {}).get(agent, {}).get("bin")
    found = shutil.which(str(configured)) if configured else None
    candidates = [Path(f) / agent for f in os.environ.get("PATH", "").split(os.pathsep)]
    for candidate in [Path(found)] if found else [] if configured else candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open("rb") as handle:
                head = handle.read(4096)
            if not (head.startswith(b"#!") and b"tmux new-" in head):
                return str(candidate)
    fail(127, f"no {agent!r} program found (tmux wrapper scripts are skipped): set "
         f'bin = "/path/to/{agent}" under [delegates.{agent}] in {path}')
'''

AGENT_EXEC = (
    r'''"""agent_exec — run one delegate headless (claude -p, codex exec) on
<run_dir>/JOB/brief.md, in the agent-kind tmux job agent_ask opened for it.
It keeps its own deadline: TERM to the child's process group, KILL 2 s later.
When the CLI reports success it writes result.md (claude's result field,
codex's -o file) and runs agent_done JOB, the only judge of DONE (exit 0).
Any other end is written to status.json in agent_done's shape (TIMEOUT,
FAILED), exit 3; stdout.txt and stderr.txt stay in <run_dir>/JOB.
Usage: agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH
  JSON states the delegate's powers, e.g. '{"claude": ["--tools", "Read"]}'.
Ref: https://docs.python.org/3/library/subprocess.html#subprocess.Popen"""
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

NAME, AGENTS = "agent_exec", ("claude", "codex")
MAX_STDOUT_BYTES = 1 << 20  # 1 MiB: the most of a delegate's stdout that is read
GRACE_S = 2.0  # after TERM, how long the group gets before KILL
'''
    + _SHARED
    + r'''

def write_atomic(path, text):
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(text)
    os.replace(partial, path)  # Ref: https://docs.python.org/3/library/os.html#os.replace


def run(argv, cwd, job_dir, seconds):
    """Run argv in its own process group with the brief on stdin. Returns its
    exit code, or None when the deadline came first. Either way the whole group
    is gone after. Ref: https://docs.python.org/3/library/os.html#os.killpg"""
    assert argv and job_dir.is_dir(), "a program and a job folder"
    with (job_dir / "brief.md").open("rb") as brief, \
            (job_dir / "stdout.txt").open("wb") as out, (job_dir / "stderr.txt").open("wb") as err:
        child = subprocess.Popen(argv, stdin=brief, stdout=out, stderr=err, cwd=cwd,
                                 start_new_session=True)
        try:
            code = child.wait(timeout=max(seconds, 0))
        except subprocess.TimeoutExpired:
            code = None
            os.killpg(child.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=GRACE_S)  # then KILL, below
        with contextlib.suppress(ProcessLookupError, PermissionError):  # already empty
            os.killpg(child.pid, signal.SIGKILL)  # leftovers: the child's own children
        child.wait()
    assert child.returncode is not None, "the child is reaped"
    return code


def answer_of(agent, stdout, last_path):
    """claude: the result field of its one JSON object; codex: its -o file."""
    assert agent in AGENTS, "two output shapes are known"
    if agent == "codex":
        answer = last_path.read_text() if last_path.is_file() else None
    else:
        try:
            data = json.loads(stdout)
        except ValueError:
            data = {}
        answer = data.get("result") if isinstance(data, dict) else None
    return answer if isinstance(answer, str) and answer.strip() else None


args = sys.argv[1:]
if len(args) != 6 or args[0] != "--exec-args":
    fail(2, "usage: agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH")
stated, agent, job, folder, deadline = args[1:]
job_ok = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job) and deadline.isdigit()
if agent not in AGENTS or not job_ok or not Path(folder).is_dir():
    fail(2, f"want {'|'.join(AGENTS)}, a job name, a folder, an epoch: {args[2:]}")
exec_args = powers(stated, [agent])[agent]
program = program_for(agent)
if shutil.which("grim") is None:
    fail(127, "program not found on PATH: 'grim' (agent_done runs through it)")
job_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run") / job
if not (job_dir / "brief.md").is_file():
    fail(2, f"no brief at {job_dir / 'brief.md'} (agent_ask writes it)")
last_path = job_dir / "last-message.md"
for stale in ("result.md", "status.json", last_path.name):  # an answer here must be THIS run's
    (job_dir / stale).unlink(missing_ok=True)
argv = [program, "-p", "--output-format", "json", *exec_args]
if agent == "codex":
    argv = [program, "exec", "--json", "-o", str(last_path), *exec_args, "-"]
until = time.strftime("%H:%M:%S", time.localtime(int(deadline)))
print(f"agent_exec: {agent} on {job} until {until}", flush=True)
code = run(argv, folder, job_dir, int(deadline) - time.time())
with (job_dir / "stdout.txt").open("rb") as handle:
    answer = answer_of(agent, handle.read(MAX_STDOUT_BYTES).decode("utf-8", "replace"), last_path)
if code == 0 and answer:  # the CLI says it succeeded; agent_done alone decides DONE
    write_atomic(job_dir / "result.md", answer)
    done = subprocess.run(["grim", "run", "agent_done", "--", job], capture_output=True, text=True)
    print(done.stdout + done.stderr, end="")
    if done.returncode == 0:
        sys.exit(0)
# agent_done's keys (job, state, stamped_at), plus what tells this end apart.
state = "TIMEOUT" if code is None else "FAILED"
status = {"job": job, "state": state, "stamped_at": int(time.time()), "agent": agent,
          "exit_code": code}
write_atomic(job_dir / "status.json", json.dumps(status) + "\n")
print(f"agent_exec: {job} {status['state']} · exit {code}")
sys.exit(3)
'''
)

# (name, description, body) rows, as in bodies_tmux.py.
HEADLESS_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_exec",
        "run a delegate headless on <run_dir>/JOB/brief.md with stated powers (agent_ask "
        "starts it): agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH — 0 only for DONE",
        AGENT_EXEC,
    ),
)
