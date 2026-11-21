"""Runtime tests for the delegate seeds in seeds/bodies_agent.py (feature 4,
docs/features/04-delegates.md), dispatched exactly as `grim run` would. The
delegate is a fake TUI (a raw-mode script that takes bracketed pastes), wired
in through [delegates.AGENT] bin in a test config file, on a private tmux
server. No real agent is started and no model is called."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="these seeds drive tmux")

_TIMEOUT_S = 30.0
_USAGE_EXIT_CODE = 2
_FAILED_EXIT_CODE = 1
_NOT_FOUND_EXIT_CODE = 127
_ARGS = ["--permission-mode", "plan"]  # what the test config grants every delegate
_NO_ANSWER_EXIT_CODE = 3
_TIMED_OUT_EXIT_CODE = 124
_ANSWER = (  # an answer agent_done passes: the five headings and a numbered claim
    "## Verdict\nThe answer is 42.\n\n## Answer\n"
    + "It was computed, then checked. " * 4
    + "\n\n## Claims\n1. 6 x 7 = 42 — evidence: `python -c 'print(6*7)'` → 42\n\n"
    "## Confidence\nhigh — arithmetic\n\n## Open\nnothing\n"
)

# The fake delegate. It logs its argv and cwd, optionally shows claude's trust
# dialog (default choice "No, exit"), then takes messages: one per Enter that
# is not inside a bracketed paste. $FAKE_REPLY picks what the first message
# does; later ones are echoed. It signals "fake-handled" once its reply is on
# screen, so a test never reads early.
_FAKE_AGENT = r"""
import json, os, pathlib, re, subprocess, sys, tty
log = pathlib.Path(os.environ["FAKE_AGENT_LOG"])
def note(**entry):
    with log.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
def show(text):
    os.write(1, text.encode() + b"\r\n")
    while text not in subprocess.run(["tmux", "capture-pane", "-p"],
                                     capture_output=True, text=True).stdout:
        pass
note(argv=sys.argv[1:], cwd=os.getcwd())
fd = sys.stdin.fileno()
tty.setraw(fd)
if os.environ.get("FAKE_DIALOG") == "trust":
    show("> No, exit")
    show("  Yes, I trust this folder")
    chose_yes = False
    while True:
        keys = os.read(fd, 65536)
        chose_yes = chose_yes or b"\x1b[B" in keys or b"\x1bOB" in keys
        if b"\r" in keys:
            break
    if not chose_yes:
        sys.exit(7)
    os.write(1, b"\x1b[2J\x1b[H")
if os.environ.get("FAKE_DIALOG") == "update":  # codex's offer, labels run together
    show("1.Updatenow")
    show("2.Skip3.Skipuntilnextversion")
    if os.read(fd, 65536) != b"\x1b":  # anything but Escape takes the update and quits
        sys.exit(7)
    os.write(1, b"\x1b[2J\x1b[H")
os.write(1, b"\x1b[?2004h")
show("? for shortcuts")
data, message, in_paste, seen = b"", b"", False, 0
while True:
    data += os.read(fd, 65536)
    while data:
        if data.startswith(b"\x1b[200~") or data.startswith(b"\x1b[201~"):
            in_paste, data = data.startswith(b"\x1b[200~"), data[6:]
        elif data[:1] == b"\x1b" and len(data) < 6:
            break
        elif data[:1] == b"\r" and not in_paste:
            text = message.replace(b"\r", b"\n").decode()
            message, data, seen = b"", data[1:], seen + 1
            note(message=text)
            reply = os.environ.get("FAKE_REPLY", "idle") if seen == 1 else "echo"
            if reply not in ("eat", "osc"):  # a real TUI shows the message it took
                show("> " + " ".join(text.split())[:60])
            if reply == "done":  # follow the done contract: write, then agent_done
                answer = re.search(r"(?i)write your answer to (\S+)", text).group(1)
                pathlib.Path(answer).write_text(os.environ["FAKE_ANSWER"])
                job = re.search(r"run: grim run agent_done -- (\S+)", text).group(1)
                subprocess.run([sys.executable, os.environ["FAKE_DONE_SCRIPT"], job], check=True)
                show("all done")
            elif reply == "ask":
                show("Do you want to delete the repo?")
                show("Enter to confirm - Esc to cancel")
            elif reply == "work":
                show("thinking hard (esc to interrupt)")
            elif reply == "exit":
                sys.exit(3)
            elif reply == "quit":  # exit 0 with no answer: the 2026-11-19 race
                sys.exit(0)
            elif reply == "eat":  # a dialog took the Enter: the message is lost
                show("Update ready - press any key")
            elif reply == "osc":  # as codex: only an OSC 7501 says it is working
                os.write(1, b"\x1b]7501;state=working:app=codex\x07")
            elif reply == "echo":
                show("you said: " + text)
            else:
                show("ok, did some things")
            subprocess.run(["tmux", "wait-for", "-S", "fake-handled"])
        else:
            message, data = message + data[:1], data[1:]
"""


def _run(name: str, argv: list[str], stdin: str | None = None) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == name)
    request = ExecutionRequest(argv=argv, stdin=stdin, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["tmux", "-L", socket, *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_S)


def _entries(tmp_path: Path) -> list[dict[str, object]]:
    lines = (tmp_path / "fake.log").read_text().splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def socket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    fake = tmp_path / "fake-agent"
    fake.write_text(f"#!{sys.executable}\n{_FAKE_AGENT}")
    fake.chmod(0o755)
    config = tmp_path / "config.toml"
    tables = [
        f'[delegates.{agent}]\nbin = "{fake}"\nargs = {json.dumps(_ARGS)}\n'
        for agent in ("claude", "codex", "pi")
    ]
    config.write_text("\n".join(tables))
    # The pane has no seeded grim db, so the fake runs the agent_done body itself.
    done_script = tmp_path / "agent_done.py"
    done_script.write_text(next(s.body for s in SEEDS if s.name == "agent_done"))
    name = f"grimtest-{tmp_path.parent.name}-{tmp_path.name}"
    for key, value in {
        "GRIM_TMUX_SOCKET": name,
        "GRIM_RUN_DIR": str(tmp_path / "run"),
        "GRIM_DELEGATES_CONFIG": str(config),
        "FAKE_AGENT_LOG": str(tmp_path / "fake.log"),
        "FAKE_ANSWER": _ANSWER,
        "FAKE_DONE_SCRIPT": str(done_script),
    }.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "work").mkdir()
    yield name
    _tmux(name, "kill-server")
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"
    (socket_dir / name).unlink(missing_ok=True)


@pytest.mark.parametrize("agent", ["claude", "codex", "pi"])
def test_agent_start_runs_any_agent_and_pastes_the_brief_as_one_message(
    socket: str, tmp_path: Path, agent: str
) -> None:
    brief = "\n".join(f"step {i}: use --dangerously-skip-permissions" for i in range(40))

    result = _run("agent_start", [agent, "fix-auth", str(tmp_path / "work")], stdin=brief)

    assert result.exit_code == 0, result.stderr
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    tags = "#{window_name} #{@grim_kind} #{@grim_job} #{@grim_agent}"
    assert _tmux(socket, "list-windows", "-t", "=grim", "-F", tags).stdout == (
        f"fix-auth agent fix-auth {agent}\n"
    )
    started, *messages = _entries(tmp_path)
    assert started == {"argv": _ARGS, "cwd": str((tmp_path / "work").resolve())}
    assert len(messages) == 1  # 40 lines, one message: the brief arrived whole
    contract = (
        f"Write your answer to {tmp_path / 'run' / 'fix-auth' / 'result.md'} with the headings "
        "above, then run: grim run agent_done -- fix-auth (it checks the file; fix what it "
        "reports)."
    )
    assert messages[0]["message"] == f"{brief}\n\n{contract}"
    assert (tmp_path / "run" / "fix-auth" / "brief.md").read_text() == f"{brief}\n\n{contract}\n"


def test_agent_start_answers_the_trust_dialog_before_it_sends_the_brief(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DIALOG", "trust")  # Enter alone would pick "No, exit"
    result = _run("agent_start", ["claude", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == 0, result.stderr
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    messages = _entries(tmp_path)[1:]
    assert [str(entry["message"]).splitlines()[0] for entry in messages] == ["do it"]


def test_agent_start_exits_1_when_a_dialog_eats_the_enter_and_never_says_started(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (delegation-plan 7a): "started" was printed for a brief never taken.
    monkeypatch.setenv("FAKE_REPLY", "eat")
    result = _run("agent_start", ["claude", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == _FAILED_EXIT_CODE
    assert "brief NOT taken" in result.stderr
    assert "started" not in result.stdout


def test_agent_start_takes_codex_osc_working_mark_as_the_brief_landing(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "osc")  # no echo, no screen mark: only the OSC
    result = _run("agent_start", ["codex", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("started grim:fix-auth (codex)")


def test_agent_start_reports_a_delegate_that_exits_at_once(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[delegates.claude]\nbin = "false"\n')
    result = _run("agent_start", ["claude", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == _FAILED_EXIT_CODE
    assert "claude exited at once (exit 1)" in result.stderr


def test_agent_start_skips_a_tmux_wrapper_script_on_path(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.toml").write_text("")  # no bin: the seed must search PATH itself
    wrapper = tmp_path / "bin" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text('#!/bin/sh\nexec tmux new-window -n claude /real/claude "$@"\n')
    wrapper.chmod(0o755)
    for tool in ("uv", "tmux"):  # a PATH with the wrapper as its only "claude"
        (wrapper.parent / tool).symlink_to(str(shutil.which(tool)))
    monkeypatch.setenv("PATH", os.pathsep.join([str(wrapper.parent), "/usr/bin", "/bin"]))
    result = _run("agent_start", ["claude", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == _NOT_FOUND_EXIT_CODE
    assert "tmux wrapper scripts are skipped" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0  # nothing was started


@pytest.mark.parametrize(
    "case",
    [
        (["claude", "job"], "brief", "usage: agent_start AGENT JOB DIR"),
        (["claude", "job", "{work}", "--dangerously-skip-permissions"], "brief", "usage: agent_"),
        (["gemini", "job", "{work}"], "brief", "unknown agent 'gemini'"),
        (["claude", "bad name", "{work}"], "brief", "bad job name"),
        (["claude", "job", "{work}/missing"], "brief", "not a directory"),
        (["claude", "job", "{work}"], "  ", "the brief goes on stdin"),
    ],
    ids=["no-dir", "extra-flag", "unknown-agent", "bad-name", "no-such-dir", "no-brief"],
)
def test_agent_start_wrong_calls_exit_2_and_start_nothing(
    socket: str, tmp_path: Path, case: tuple[list[str], str, str]
) -> None:
    argv, stdin, said = case
    result = _run("agent_start", [a.format(work=tmp_path / "work") for a in argv], stdin=stdin)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert said in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0


def _start(socket: str, tmp_path: Path) -> None:
    """Start delegate fix-auth with a one-line brief."""
    started = _run("agent_start", ["claude", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert started.exit_code == 0, started.stderr


def test_agent_read_gives_the_text_of_result_md_once_the_delegate_signals(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "done")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0  # the delegate's own signal
    result = _run("agent_read", ["fix-auth"])
    assert result.exit_code == 0, result.stderr
    status, *answer = result.stdout.splitlines()
    assert status.startswith("[agent_read] fix-auth: DONE · result.md follows")
    assert answer == _ANSWER.rstrip("\n").splitlines()  # the file, not the screen


@pytest.mark.parametrize(
    "case",
    [
        ("idle", "IDLE", "ok, did some things"),
        ("ask", "NEEDS-INPUT", "Do you want to delete the repo?"),
        ("work", "WORKING", "thinking hard"),
    ],
)
def test_agent_read_names_the_state_and_shows_the_screen_when_there_is_no_result(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[str, str, str]
) -> None:
    reply, state, shown = case
    monkeypatch.setenv("FAKE_REPLY", reply)
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    result = _run("agent_read", ["fix-auth"])
    assert result.exit_code == 0, result.stderr
    status, *screen = result.stdout.splitlines()
    assert status == f"[agent_read] fix-auth: {state} · no result.md · its screen follows"
    assert any(shown in line for line in screen)


def test_agent_read_reports_a_delegate_that_exited_without_a_result(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "exit")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0  # sent by the job-end hook
    result = _run("agent_read", ["fix-auth"])
    assert result.exit_code == _NO_ANSWER_EXIT_CODE  # ended without an answer: not a success
    assert (
        result.stdout.splitlines()[0]
        == "[agent_read] fix-auth: EXITED (exit 3) · no result.md · the tail of its log follows"
    )


def test_a_human_can_answer_in_the_pane_and_the_agent_can_follow_up(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "ask")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    for keys in (["-l", "yes"], ["Enter"]):  # the human, typing in the delegate's pane
        assert _tmux(socket, "send-keys", "-t", "=grim:fix-auth", *keys).returncode == 0
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    assert "you said: yes" in _run("agent_read", ["fix-auth"]).stdout
    assert _run("tmux_send", ["fix-auth", "now run the full suite"]).exit_code == 0
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    assert "you said: now run the full suite" in _run("agent_read", ["fix-auth"]).stdout


def test_a_delegate_is_listed_and_stopped_like_any_job_and_keeps_its_result(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "done")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0
    assert _run("tmux_list", []).stdout.startswith("fix-auth  agent  alive  age ")
    assert _run("tmux_stop", ["fix-auth"]).exit_code == 0
    assert _run("tmux_list", []).stdout == "no jobs\n"
    assert (tmp_path / "run" / "fix-auth" / "result.md").read_text() == _ANSWER
    kept = _run("agent_read", ["fix-auth"])  # the window is gone; the checked answer is not
    assert kept.exit_code == 0, kept.stderr
    assert kept.stdout.startswith("[agent_read] fix-auth: DONE · result.md follows")


def test_agent_read_refuses_a_plain_job_and_wrong_calls(socket: str) -> None:
    assert _run("tmux_start", ["api", "sleep", "600"]).exit_code == 0
    assert "is not a delegate" in _run("agent_done", ["api"]).stderr
    assert _run("agent_done", []).exit_code == _USAGE_EXIT_CODE
    plain = _run("agent_read", ["api"])
    assert plain.exit_code == _FAILED_EXIT_CODE
    assert "job 'api' is not a delegate (kind 'job'); read it with tmux_read api" in plain.stderr
    for argv in ([], ["api", "0"], ["api", "many"], ["api", "401"]):
        wrong = _run("agent_read", argv)
        assert wrong.exit_code == _USAGE_EXIT_CODE
        assert "usage: agent_read JOB [LINES]" in wrong.stderr


def test_agent_start_without_the_agent_program_exits_127_naming_it(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.toml").write_text("")  # no bin: codex must be found on PATH
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("uv", "tmux"):
        (bin_dir / tool).symlink_to(str(shutil.which(tool)))
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))
    result = _run("agent_start", ["codex", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == _NOT_FOUND_EXIT_CODE
    assert "no 'codex' program found on PATH" in result.stderr
    assert _tmux(socket, "has-session", "-t", "=grim").returncode != 0  # nothing half-started


# Characterization tests (delegation-plan step 1b): they pin today's
# behavior — including the "success that isn't" the plan removes — so the
# behavior change in steps 1d and 1e shows up as a diff of these asserts.
def test_tmux_wait_on_a_delegate_that_quit_0_without_an_answer_exits_3(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "quit")  # exit 0, no result.md: the race's false success
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0  # the job-end hook
    waited = _run("tmux_wait", ["grim-fix-auth", "5"])
    assert waited.exit_code == _NO_ANSWER_EXIT_CODE, waited.stderr
    assert waited.stdout == "job 'fix-auth' EXITED (exit 0): no result: nothing to wait for\n"


def test_a_second_tmux_wait_on_a_checked_delegate_returns_at_once(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "done")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0  # this waiter eats the wake
    for _ in range(2):  # the signal is spent, and the delegate is still alive: the stamp answers
        waited = _run("tmux_wait", ["grim-fix-auth", "5"])
        assert waited.exit_code == 0, waited.stderr
        assert waited.stdout.startswith("job 'fix-auth' is done (agent_done checked its answer)")


def test_agent_read_calls_a_result_agent_done_never_passed_unchecked(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REPLY", "idle")
    _start(socket, tmp_path)
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    (tmp_path / "run" / "fix-auth" / "result.md").write_text("")  # empty: no answer at all
    result = _run("agent_read", ["fix-auth"])
    assert result.exit_code == 0, result.stderr  # still live: it may yet run agent_done
    assert result.stdout.splitlines()[0] == (
        "[agent_read] fix-auth: UNCHECKED · result UNCHECKED · its screen follows"
    )


def test_agent_start_skips_codex_update_offer_even_when_its_labels_run_together(
    socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (delegation-plan step 1c): codex drew "2.Skip3.Skipuntilnextversion"
    # with no spaces, the dialog went unanswered, and Enter took the update.
    monkeypatch.setenv("FAKE_DIALOG", "update")
    result = _run("agent_start", ["codex", "fix-auth", str(tmp_path / "work")], stdin="do it")
    assert result.exit_code == 0, result.stderr
    assert _tmux(socket, "wait-for", "fake-handled").returncode == 0
    messages = _entries(tmp_path)[1:]
    assert [str(entry["message"]).splitlines()[0] for entry in messages] == ["do it"]


@pytest.mark.parametrize(
    "case",
    [("", "result.md is 0 bytes"), (_ANSWER.replace("## Open", "## Todo"), "heading: ## Open")],
    ids=["empty", "missing-heading"],
)
def test_agent_done_refuses_a_bad_answer_and_marks_nothing(
    socket: str, tmp_path: Path, case: tuple[str, str]
) -> None:
    text, named = case
    _start(socket, tmp_path)
    (tmp_path / "run" / "fix-auth" / "result.md").write_text(text)
    result = _run("agent_done", ["fix-auth"])
    assert result.exit_code == _FAILED_EXIT_CODE
    assert named in result.stderr
    assert not (tmp_path / "run" / "fix-auth" / "status.json").exists()
    assert _run("tmux_wait", ["grim-fix-auth", "0.3"]).exit_code == _TIMED_OUT_EXIT_CODE


def test_agent_done_stamps_a_good_answer_with_its_hash_then_signals(
    socket: str, tmp_path: Path
) -> None:
    _start(socket, tmp_path)
    result_path = tmp_path / "run" / "fix-auth" / "result.md"
    result_path.write_text(_ANSWER)
    done = _run("agent_done", ["fix-auth"])
    assert done.exit_code == 0, done.stderr
    record = json.loads((result_path.parent / "status.json").read_text())
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert (record["state"], record["sha256"]) == ("DONE", digest)
    shown = _tmux(socket, "show-options", "-w", "-v", "-t", "=grim:fix-auth", "@grim_done")
    assert shown.stdout.strip() == f"{record['stamped_at']}:{record['bytes']}:{digest[:8]}"
    assert _tmux(socket, "wait-for", "grim-fix-auth").returncode == 0  # the wake is there
