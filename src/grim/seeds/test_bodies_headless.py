"""Runtime tests for seeds/bodies_headless.py: a fake delegate (config bin), a private
tmux server and a throwaway library for agent_done. No real agent, no model call."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 30.0
_USAGE_EXIT_CODE = 2
_FAILED_EXIT_CODE = 3
_NOT_FOUND_EXIT_CODE = 127
_POWERS = ["--tools", "Read"]
_MAX_POLLS = 5000  # a bounded busy-wait on pgrep; each poll takes milliseconds
_ANSWER = (  # an answer agent_done passes: the five headings and a numbered claim
    "## Verdict\nThe answer is 42.\n\n## Answer\n"
    + "It was computed, then checked. " * 4
    + "\n\n## Claims\n1. 6 x 7 = 42 — evidence: `python -c 'print(6*7)'` → 42\n\n"
    "## Confidence\nhigh — arithmetic\n\n## Open\nnothing\n"
)
# The fake claude/codex: logs its argv, stdin and process group, then acts out
# $FAKE_MODE. codex is told apart by its first argument, "exec".
_FAKE = r"""
import json, os, pathlib, subprocess, sys, time
mode, brief = os.environ.get("FAKE_MODE", "valid"), sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "brief": brief, "pgid": os.getpgrp()}) + "\n")
if mode in ("boom", "login", "context"):
    hint = {"login": "please login", "context": "prompt is too long"}.get(mode, "")
    sys.exit("something broke " + hint)
if mode == "sleep":  # a grandchild in the same group, then outlive the deadline
    subprocess.Popen(["sleep", "60"])
    time.sleep(60)
canned = {"prose": "Just prose, no headings. " * 12, "blank": " "}
answer = canned.get(mode, os.environ["FAKE_ANSWER"])
usage = {"input_tokens": 9, "output_tokens": 4}
if sys.argv[1] == "exec":
    pathlib.Path(sys.argv[sys.argv.index("-o") + 1]).write_text(answer)
    print(json.dumps({"type": "turn.completed", "usage": usage}))
else:
    print(json.dumps({"result": answer, "total_cost_usd": 0.0123, "usage": usage}))
"""


def _run(name: str, argv: list[str], stdin: str | None = None) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=stdin, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _log(tmp_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (tmp_path / "fake.log").read_text().splitlines()]


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    fake, config = tmp_path / "fake-agent", tmp_path / "config.toml"
    fake.write_text(f"#!{sys.executable}\n{_FAKE}")
    fake.chmod(0o755)
    config.write_text("".join(f'[delegates.{a}]\nbin = "{fake}"\n' for a in ("claude", "codex")))
    for key, value in {
        "GRIM_TMUX_SOCKET": name,
        "GRIM_RUN_DIR": str(tmp_path / "run"),
        "GRIM_DB": str(tmp_path / "grimoire.db"),
        "GRIM_DELEGATES_CONFIG": str(config),
        "FAKE_LOG": str(tmp_path / "fake.log"),
        "FAKE_ANSWER": _ANSWER,
    }.items():
        monkeypatch.setenv(key, value)
    assert subprocess.run(["grim", "init"], capture_output=True, check=False).returncode == 0
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, check=False)
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


def _exec(tmp_path: Path, agent: str = "claude", brief: str = "Say 42.\n") -> tuple[int, Path]:
    """agent_exec AGENT q-AGENT beside the agent-kind window agent_done looks up, with
    a deadline 20 s out (2 s for FAKE_MODE=sleep). Returns (its exit code, the job dir)."""
    job, seconds = f"q-{agent}", 2 if os.environ.get("FAKE_MODE") == "sleep" else 20
    (tmp_path / "run" / job).mkdir(parents=True)
    (tmp_path / "run" / job / "brief.md").write_text(brief)
    assert _run("tmux_start", ["--kind", "agent", job, "sleep", "600"]).exit_code == 0
    stated, deadline = json.dumps({agent: _POWERS}), str(int(time.time()) + seconds)
    ran = _run("agent_exec", ["--exec-args", stated, agent, job, str(tmp_path), deadline])
    return ran.exit_code, tmp_path / "run" / job


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_agent_exec_hands_a_good_answer_to_agent_done_and_is_done(
    socket: str, tmp_path: Path, agent: str
) -> None:
    exit_code, job_dir = _exec(tmp_path, agent)
    assert exit_code == 0
    status = json.loads((job_dir / "status.json").read_text())
    assert status["state"] == "DONE"
    assert status["sha256"] == hashlib.sha256(_ANSWER.encode()).hexdigest()  # agent_done's
    assert (job_dir / "result.md").read_text() == _ANSWER
    want = ["-p", "--output-format", "json", *_POWERS]
    if agent == "codex":
        want = ["exec", "--json", "-o", str(job_dir / "last-message.md"), *_POWERS, "-"]
    assert _log(tmp_path)[0]["argv"] == want
    meta = json.loads((job_dir / "meta.json").read_text())
    cost = None if agent == "codex" else 0.0123  # codex reports tokens, never a price
    assert (meta["cost_usd"], meta["input_tokens"], meta["output_tokens"]) == (cost, 9, 4)
    assert meta["dir"] == str(tmp_path.resolve())  # agent_compare checks evidence under it


def test_agent_exec_past_the_deadline_is_timeout_and_its_process_group_is_gone(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "sleep")
    exit_code, job_dir = _exec(tmp_path)
    assert exit_code == _FAILED_EXIT_CODE
    assert json.loads((job_dir / "status.json").read_text())["state"] == "TIMEOUT"
    group = str(_log(tmp_path)[0]["pgid"])
    gone = (
        subprocess.run(["pgrep", "-g", group], capture_output=True).returncode
        for _ in range(_MAX_POLLS)
    )
    assert any(code == 1 for code in gone), f"process group {group} still has members"


def test_a_long_brief_arrives_whole_on_the_delegates_stdin(socket: str, tmp_path: Path) -> None:
    brief = "".join(f"line {n}: keep every word, even 'quotes' and $VARS\n" for n in range(40))
    exit_code, _job_dir = _exec(tmp_path, brief=brief)
    assert exit_code == 0
    assert _log(tmp_path)[0]["brief"] == brief


def test_agent_exec_names_a_failed_cli_and_keeps_its_output(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "boom")
    exit_code, job_dir = _exec(tmp_path)
    assert exit_code == _FAILED_EXIT_CODE
    status = json.loads((job_dir / "status.json").read_text())
    assert (status["state"], status["exit_code"], status["agent"]) == ("FAILED", 1, "claude")
    assert "something broke" in (job_dir / "stderr.txt").read_text()
    assert not (job_dir / "result.md").exists()


@pytest.mark.parametrize(
    "case",
    [
        ("login", "AUTH"),
        ("context", "CONTEXT"),
        ("prose", "INVALID_RESULT"),
        ("blank", "INVALID_RESULT"),
    ],
    ids=str,
)
def test_agent_exec_names_how_a_delegate_failed(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[str, str]
) -> None:
    mode, state = case
    monkeypatch.setenv("FAKE_MODE", mode)
    exit_code, job_dir = _exec(tmp_path)
    assert exit_code == _FAILED_EXIT_CODE
    assert json.loads((job_dir / "status.json").read_text())["state"] == state
    assert json.loads((job_dir / "meta.json").read_text())["brief_bytes"] == len("Say 42.\n")


@pytest.mark.parametrize("stated", ["{}", "not json", '{"claude": "--tools"}', '{"codex": []}'])
def test_a_delegate_without_stated_powers_exits_2_and_never_starts(
    socket: str, tmp_path: Path, stated: str
) -> None:
    (tmp_path / "run" / "q").mkdir(parents=True)
    (tmp_path / "run" / "q" / "brief.md").write_text("Say 42.\n")
    argv = ["--exec-args", stated, "claude", "q", str(tmp_path), str(int(time.time()) + 60)]
    result = _run("agent_exec", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "powers must be stated" in result.stderr
    assert not (tmp_path / "fake.log").exists()


@pytest.mark.parametrize(
    "argv", [[], ["claude", "j", "/tmp", "1"], ["--exec-args", "{}", "pi", "j", "/tmp", "1"]]
)
def test_agent_exec_wrong_calls_exit_2(socket: str, argv: list[str]) -> None:
    result = _run("agent_exec", argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "agent_exec" in result.stderr


def _tmux(socket: str, *args: str) -> str:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=_TIMEOUT_S).stdout


def test_agent_ask_both_opens_two_agent_windows_and_both_see_the_same_brief(
    socket: str, tmp_path: Path
) -> None:
    stated = json.dumps({"claude": _POWERS, "codex": _POWERS})
    asked = _run("agent_ask", ["--exec-args", stated, "both", "q", str(tmp_path), "60"], "Hi.\n")
    assert asked.exit_code == 0, asked.stderr
    assert asked.stdout.startswith("started q-claude q-codex · deadline ")
    windows = _tmux(socket, "list-windows", "-t", "=grim", "-F", "#{@grim_job} #{@grim_kind}")
    assert sorted(windows.splitlines()) == ["q-claude agent", "q-codex agent"]
    for job in ("q-claude", "q-codex"):  # agent_done signals each once it passes the answer
        _tmux(socket, "wait-for", f"grim-{job}")
        assert json.loads((tmp_path / "run" / job / "status.json").read_text())["state"] == "DONE"
    briefs = [entry["brief"] for entry in _log(tmp_path)]
    assert len(briefs) == len(set(briefs)) * 2  # two delegates, one identical brief
    assert str(briefs[0]).startswith("Hi.\n\nAnswer in markdown")


def test_agent_ask_with_codex_missing_exits_127_and_opens_no_window(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "gone.toml"
    config.write_text(
        f'[delegates.claude]\nbin = "{tmp_path / "fake-agent"}"\n'
        f'[delegates.codex]\nbin = "{tmp_path / "no-such-codex"}"\n'
    )
    monkeypatch.setenv("GRIM_DELEGATES_CONFIG", str(config))
    stated = json.dumps({"claude": _POWERS, "codex": _POWERS})
    asked = _run("agent_ask", ["--exec-args", stated, "both", "q", str(tmp_path)], "Hi.\n")
    assert asked.exit_code == _NOT_FOUND_EXIT_CODE
    assert "'codex'" in asked.stderr
    assert _tmux(socket, "list-windows", "-a") == ""
    assert not (tmp_path / "run" / "q-claude").exists()


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--exec-args"],
        ["--exec-args", "{}", "both", "q", "/tmp", "9999"],
        ["both", "q", "/tmp"],
    ],
)
def test_agent_ask_wrong_calls_exit_2(socket: str, argv: list[str]) -> None:
    result = _run("agent_ask", argv, "Hi.\n")
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "agent_ask" in result.stderr
