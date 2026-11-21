"""tmux-latency bench (docs/delegation-plan.md 5a, R3): each tmux move's time at the adapter
tool span — GrimEnvironment.execute({"tool": "run"}), the in-process cli.main entry the agent
uses — and the tmux clients it starts. The parallel case calls exec.dispatch directly (cli.main
swaps sys.stdout, so threads would garble it). All private: temp GRIM_DB and GRIM_RUN_DIR, tmux
`-L grimbench`, counting shim/tmux first on PATH (--no-shim: time without it). One JSON row per
case: p50/p95/p99 ms, failures, clients per call. The real grim session is never touched.
Usage: uv run python evals/tmux-latency/bench.py [--warm 300] [--cases 30] [--out F] [--no-shim]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SOCKET = "grimbench"
SHIM_DIR = Path(__file__).resolve().parent / "shim"
SEEDS = "tmux_start,tmux_list,tmux_read,tmux_send,tmux_stop"
WINDOWS = 100
LOG_BYTES = 50 * 1024 * 1024
PARALLEL = 4
SLEEPER = ("sleep", "600")  # outlives the bench; kill-server ends it
WAIT_S = 20
MIN_SAMPLES = 2  # statistics.quantiles needs two points
Sample = tuple[float, int, float]  # (ms, exit code, tmux clients)


def isolate(tmp: Path, *, shim: bool) -> None:
    """Point every grim path and the tmux server at private places; shim counts clients."""
    real_tmux = shutil.which("tmux")
    assert real_tmux and SHIM_DIR.is_dir(), "the bench needs tmux and its shim"
    for name in ("GRIM_SESSION", "GRIM_CWD", "GRIM_CALL_DEPTH", "GRIM_TIME_BUDGET_S"):
        os.environ.pop(name, None)
    os.environ.update(
        GRIM_DB=str(tmp / "grimoire.db"),
        GRIM_RUN_DIR=str(tmp / "run"),
        GRIM_TMUX_SOCKET=SOCKET,
        GRIM_BASE_SEEDS=SEEDS,
        GRIM_JOBS_LINE="0",  # the jobs line is outside the tool span anyway
        GRIM_INSTRUMENT_FILE=str(tmp / "spans.jsonl"),
        GRIM_COMPLETIONS_DIR=str(tmp / "completions"),
        GRIM_BASHRC=str(tmp / ".bashrc"),
        GRIM_ZSHRC=str(tmp / ".zshrc"),
        GRIM_BENCH_COUNT=str(tmp / "clients"),
        GRIM_BENCH_REAL_TMUX=real_tmux,
    )
    if shim:
        os.environ["PATH"] = f"{SHIM_DIR}{os.pathsep}{os.environ['PATH']}"
    Path(os.environ["GRIM_BENCH_COUNT"]).write_text("")
    assert (shutil.which("tmux") == str(SHIM_DIR / "tmux")) == shim, "the shim leads PATH"


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """The bench's own tmux calls go to the real binary, so they are never counted."""
    real = [os.environ["GRIM_BENCH_REAL_TMUX"], "-L", SOCKET, *args]
    return subprocess.run(real, capture_output=True, text=True, check=False, timeout=WAIT_S)


def clients() -> int:
    return Path(os.environ["GRIM_BENCH_COUNT"]).stat().st_size


def call(env: Any, name: str, *args: str) -> Sample:
    """One `run NAME ARGS` through the adapter: (ms, exit code, tmux clients)."""
    before = clients()
    started = time.perf_counter()
    out = env.execute({"tool": "run", "args": {"name": name, "args": list(args)}})
    ms = (time.perf_counter() - started) * 1000
    assert ms >= 0 and clients() >= before, "time and the client count only grow"
    if out["returncode"] != 0:  # a failure is counted, and its tail is shown
        print(f"[bench] {name} {' '.join(args)}: {out['output'][-300:]}", file=sys.stderr)
    return ms, int(out["returncode"]), float(clients() - before)


def stats(samples: list[Sample]) -> dict[str, float]:
    """p50/p95/p99 in ms, failures (non-zero exits), mean tmux clients per call."""
    assert len(samples) >= MIN_SAMPLES, "a percentile needs at least two samples"
    cut = statistics.quantiles([s[0] for s in samples], n=100, method="inclusive")
    failures = sum(1 for s in samples if s[1] != 0)
    mean_clients = statistics.fmean(s[2] for s in samples)
    cuts = {k: round(cut[q], 1) for k, q in (("p50_ms", 49), ("p95_ms", 94), ("p99_ms", 98))}
    return {"n": len(samples), **cuts, "failures": failures, "clients": round(mean_clients, 2)}


def fresh(env: Any) -> None:
    """A new grimbench server holding one live `cat` job, "sink", to read and type into."""
    tmux("kill-server")
    assert call(env, "tmux_start", "sink", "cat")[1] == 0, "the sink job starts"


def warm(env: Any, n: int) -> dict[str, list[Sample]]:
    fresh(env)
    moves: dict[str, list[Sample]] = {m: [] for m in ("list", "read", "send", "start", "stop")}
    for i in range(n):
        moves["start"].append(call(env, "tmux_start", f"w{i}", *SLEEPER))
        moves["stop"].append(call(env, "tmux_stop", f"w{i}"))
        moves["list"].append(call(env, "tmux_list"))
        moves["read"].append(call(env, "tmux_read", "sink", "50"))
        moves["send"].append(call(env, "tmux_send", "sink", f"hello {i}"))
    return moves


def cases(env: Any, n: int) -> dict[str, list[Sample]]:
    found: dict[str, list[Sample]] = {"cold start": []}
    for i in range(n):  # cold: no server, so tmux_start makes the session
        tmux("kill-server")
        found["cold start"].append(call(env, "tmux_start", f"cold{i}", *SLEEPER))
    fresh(env)
    for i in range(WINDOWS - 1):
        assert call(env, "tmux_start", f"many{i}", *SLEEPER)[1] == 0, "a window opens"
    found["100-window list"] = [call(env, "tmux_list") for _ in range(n)]
    found["100-window read"] = [call(env, "tmux_read", "sink", "50") for _ in range(n)]
    fresh(env)
    assert call(env, "tmux_start", "big", "true")[1] == 0, "the big job starts"
    tmux("wait-for", "grim-big")  # its pane-died hook: dead, so its log is the record
    line = b"a log line of about sixty bytes, repeated to fill fifty MB\n"
    log = Path(os.environ["GRIM_RUN_DIR"]) / "big.log"
    log.write_bytes(line * (LOG_BYTES // len(line)))
    found["50MB-log dead read"] = [call(env, "tmux_read", "big", "50") for _ in range(n)]
    found["multiline send"] = [call(env, "tmux_send", "sink", "one\ntwo\nthree") for _ in range(n)]
    return found


def parallel(n: int) -> tuple[list[Sample], int]:
    """n rounds of PARALLEL same-name tmux_starts; returns samples and bad rounds
    (any round that made more than one window or let more than one start win)."""
    from grim.exec.dispatch import ExecutionRequest, ScriptVersion, dispatch
    from grim.seeds.bodies import SEEDS as ROSTER

    body = next(s.body for s in ROSTER if s.name == "tmux_start")
    samples: list[Sample] = []
    bad = 0

    def one(job: str) -> tuple[float, int]:
        request = ExecutionRequest(argv=[job, *SLEEPER], stdin=None, cwd=None, timeout=WAIT_S)
        started = time.perf_counter()
        result = dispatch(ScriptVersion(language="python", body=body), request)
        return (time.perf_counter() - started) * 1000, result.exit_code

    for i in range(n):
        before = clients()
        with ThreadPoolExecutor(PARALLEL) as pool:
            ends = list(pool.map(one, [f"same{i}"] * PARALLEL))
        each = (clients() - before) / PARALLEL
        made = tmux("list-windows", "-t", "=grim", "-F", "#{@grim_job}").stdout.split()
        bad += made.count(f"same{i}") != 1 or [code for _, code in ends].count(0) != 1
        samples += [(ms, 0, each) for ms, _ in ends]  # a loser's exit 1 is the right answer
    return samples, bad


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warm", type=int, default=300)
    parser.add_argument("--cases", type=int, default=30)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-shim", action="store_true", help="time without the counter")
    opts = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="grimbench-") as tmp:
        isolate(Path(tmp), shim=not opts.no_shim)
        from grim import cli
        from grim.adapter.environment import GrimEnvironment

        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(["init"]) == 0, "the private library is seeded"
        env = GrimEnvironment(cwd=tmp)
        try:
            raw = {f"warm {m}": s for m, s in warm(env, opts.warm).items()}
            raw |= cases(env, opts.cases)
            raw["same-name parallel start"], bad = parallel(opts.cases)
        finally:
            tmux("kill-server")
    results = {case: stats(samples) for case, samples in raw.items()}
    results["same-name parallel start"]["failures"] = bad
    for case, row in results.items():
        if opts.no_shim:
            del row["clients"]  # nothing counted them
        print(f"{case:<26}{json.dumps(row)}")
    if opts.out:
        opts.out.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
