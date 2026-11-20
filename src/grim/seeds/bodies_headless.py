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
            fail(2, f"powers must be stated: --exec-args '{{\"{agent}\": [\"--flag\", ...]}}' "
                 "(documented defaults: grim/seeds/delegates.toml)")
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
AUTH, CONTEXT, FAILED, INVALID_RESULT), exit 3; stdout.txt and stderr.txt
stay in <run_dir>/JOB. meta.json: exit, wall_s, cost_usd and tokens (null
when the CLI does not report them), brief_bytes.
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
TAIL_BYTES = 8192  # how much of stderr/stdout's end the failure hints are read from
AUTH_HINTS = ("login", "authenticate", "api key", "401")
CONTEXT_HINTS = ("context length", "prompt is too long")
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


def as_dict(text):
    """A JSON object, or {} for anything else: CLI output is read defensively."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def parsed(agent, stdout, last_path):
    """(answer, is_error, cost_usd, usage). claude: one JSON object with result,
    is_error, total_cost_usd, usage. codex: the -o file, and usage from the last
    JSONL event that carries one (top level or one level down). Missing: None."""
    assert agent in AGENTS, "two output shapes are known"
    data = as_dict(stdout)
    answer, errored, cost, usage = (data.get(k) for k in ("result", "is_error", "total_cost_usd",
                                                          "usage"))
    if agent == "codex":
        answer = last_path.read_text() if last_path.is_file() else None
        for event in map(as_dict, stdout.splitlines()):
            nested = [event, *event.values()]
            usage = next((v["usage"] for v in nested if isinstance(v, dict)
                          and isinstance(v.get("usage"), dict)), usage)
    answer = answer if isinstance(answer, str) and answer.strip() else None
    assert answer is None or answer.strip(), "an answer is never blank"
    return answer, errored, cost, usage if isinstance(usage, dict) else {}


def classify(code, errored, text, done_code):
    """The plan's order: TIMEOUT, AUTH, CONTEXT, FAILED, INVALID_RESULT (done_code
    None: no answer; 1: agent_done refused it). DONE is never decided here."""
    assert done_code != 0, "agent_done passed it: that is DONE, not ours to name"
    low, failed = text.lower(), code != 0 or errored is True
    if code is None:
        return "TIMEOUT"
    if failed and any(hint in low for hint in AUTH_HINTS):
        return "AUTH"
    if failed and any(hint in low for hint in CONTEXT_HINTS):
        return "CONTEXT"
    return "FAILED" if failed or done_code not in (None, 1) else "INVALID_RESULT"


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
for stale in ("result.md", "status.json", "meta.json", last_path.name):  # THIS run's only
    (job_dir / stale).unlink(missing_ok=True)
argv = [program, "-p", "--output-format", "json", *exec_args]
if agent == "codex":
    argv = [program, "exec", "--json", "-o", str(last_path), *exec_args, "-"]
until = time.strftime("%H:%M:%S", time.localtime(int(deadline)))
print(f"agent_exec: {agent} on {job} until {until}", flush=True)
started = time.monotonic()
code = run(argv, folder, job_dir, int(deadline) - time.time())
with (job_dir / "stdout.txt").open("rb") as handle:
    stdout = handle.read(MAX_STDOUT_BYTES).decode("utf-8", "replace")
answer, errored, cost, usage = parsed(agent, stdout, last_path)
meta = {"agent": agent, "exit": code, "wall_s": round(time.monotonic() - started, 1),
        "cost_usd": cost, "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "brief_bytes": (job_dir / "brief.md").stat().st_size}
write_atomic(job_dir / "meta.json", json.dumps(meta) + "\n")
done_code = None
if code == 0 and errored is not True and answer:  # success: agent_done alone decides DONE
    write_atomic(job_dir / "result.md", answer)
    done = subprocess.run(["grim", "run", "agent_done", "--", job], capture_output=True, text=True)
    print(done.stdout + done.stderr, end="")
    if done.returncode == 0:
        sys.exit(0)
    done_code = done.returncode
stderr = (job_dir / "stderr.txt").read_bytes()[-TAIL_BYTES:].decode("utf-8", "replace")
state = classify(code, errored, stderr + stdout[-TAIL_BYTES:], done_code)
# agent_done's keys (job, state, stamped_at), plus what tells this end apart.
status = {"job": job, "state": state, "stamped_at": int(time.time()), "agent": agent,
          "exit_code": code}
write_atomic(job_dir / "status.json", json.dumps(status) + "\n")
print(f"agent_exec: {job} {status['state']} · exit {code}")
sys.exit(3)
'''
)

AGENT_ASK = (
    r'''"""agent_ask — ask claude, codex or both one question, headless, at once.
Each runs as agent_exec in its own agent-kind tmux job ID-AGENT (attach with
`tmux attach -t grim` to watch), on the brief from stdin plus the answer
headings agent_done checks. Everything is checked before anything starts:
programs (127), powers and arguments (2), free job names (1).
Usage: agent_ask --exec-args JSON AGENTS ID DIR [SECONDS]   (brief on stdin)
  AGENTS: claude, codex or both. JSON states each one's powers, e.g.
  '{"claude": ["--tools", "Read"], "codex": ["--sandbox", "read-only"]}'.
  DIR: absolute; the delegates work there. SECONDS: 1-3500, default 1200.
Then: tmux_wait grim-ID-AGENT SECONDS (0 = DONE, 3 = failed; status.json in
<run_dir>/ID-AGENT says how) · agent_read ID-AGENT for the answer.
Ref: https://man.openbsd.org/tmux#list-windows"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

NAME, AGENTS, SESSION = "agent_ask", ("claude", "codex"), "grim"
DEFAULT_S, MAX_S, MAX_BRIEF_CHARS, SPARE_S = 1200, 3500, 20000, 60
CONTRACT = (  # what agent_done checks; Ref: seeds/bodies_delegate.py
    "\n\nAnswer in markdown, 200 to 16384 bytes, with exactly these headings, each with "
    "text under it: ## Verdict, ## Answer, ## Claims (numbered: 1. claim — evidence), "
    "## Confidence, ## Open."
)
'''
    + _SHARED
    + r"""

args = sys.argv[1:]
if not 5 <= len(args) <= 6 or args[0] != "--exec-args":
    fail(2, "usage: agent_ask --exec-args JSON AGENTS ID DIR [SECONDS]   (brief on stdin)")
stated, which, ask_id, folder, *rest = args[1:]
seconds = int(rest[0]) if rest and rest[0].isdigit() else -1 if rest else DEFAULT_S
agents = list(AGENTS) if which == "both" else [which]
if which not in (*AGENTS, "both") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,56}", ask_id):
    fail(2, f"want claude|codex|both and an ID (letters, digits, _ -): {which!r} {ask_id!r}")
if not os.path.isabs(folder) or not Path(folder).is_dir() or not 1 <= seconds <= MAX_S:
    fail(2, f"want an absolute folder and 1-{MAX_S} seconds: {folder!r} {rest}")
brief = sys.stdin.read().strip()
if not 0 < len(brief) <= MAX_BRIEF_CHARS:
    fail(2, f"the brief goes on stdin, 1 to {MAX_BRIEF_CHARS} chars (got {len(brief)})")
powers_of = powers(stated, agents)
for agent in agents:
    program_for(agent)  # exits 127 naming the missing program
for tool in ("tmux", "grim"):
    if shutil.which(tool) is None:
        fail(127, f"program not found on PATH: {tool!r}")
socket = os.environ.get("GRIM_TMUX_SOCKET")
listed = subprocess.run([*(["tmux", "-L", socket] if socket else ["tmux"]), "list-windows",
                         "-t", f"={SESSION}", "-F", "#{@grim_job}"], capture_output=True, text=True)
jobs = [f"{ask_id}-{agent}" for agent in agents]
taken = sorted(set(jobs) & set(listed.stdout.split()))
if taken:
    fail(1, f"job names taken: {', '.join(taken)}; pick another ID or tmux_stop them")

deadline = int(time.time()) + seconds  # once, so both delegates share one deadline
run_dir = Path(os.environ.get("GRIM_RUN_DIR") or Path.home() / ".grimoire" / "run")
started = []
for agent, job in zip(agents, jobs, strict=True):
    (run_dir / job).mkdir(parents=True, exist_ok=True)
    (run_dir / job / "brief.md").write_text(brief + CONTRACT + "\n")
    command = ["grim", "run", "tmux_start", "--", "--kind", "agent", job, "grim", "run",
               "--timeout", str(seconds + SPARE_S), "agent_exec", "--", "--exec-args",
               json.dumps({agent: powers_of[agent]}), agent, job, folder, str(deadline)]
    opened = subprocess.run(command, cwd=folder, capture_output=True, text=True)
    if opened.returncode != 0:  # undo the half-start: nothing is left running
        for done in started:
            subprocess.run(["grim", "run", "tmux_stop", "--", done], capture_output=True)
        fail(1, f"could not start {job}: {opened.stdout.strip()} {opened.stderr.strip()}")
    started.append(job)
until = time.strftime("%H:%M:%S", time.localtime(deadline))
print(f"started {' '.join(started)} · deadline {until} · next: tmux_wait grim-JOB {seconds} "
      "for each, then agent_read JOB")
"""
)

# (name, description, body) rows, as in bodies_tmux.py.
HEADLESS_SEEDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_exec",
        "run a delegate headless on <run_dir>/JOB/brief.md with stated powers (agent_ask "
        "starts it): agent_exec --exec-args JSON AGENT JOB DIR DEADLINE_EPOCH — 0 only for DONE",
        AGENT_EXEC,
    ),
    (
        "agent_ask",
        "ask claude, codex or both one question headless, each in its own tmux job: "
        "agent_ask --exec-args JSON AGENTS ID DIR [SECONDS] (brief on stdin)",
        AGENT_ASK,
    ),
)
