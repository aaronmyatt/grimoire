"""Offline tests for the grim-agent launcher. The pure arg builder is tested
directly; main() is tested with mini's Typer app stubbed out, so no model is
queried and the agent loop never runs."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from grim import db

pytest.importorskip("minisweagent")  # launcher.main imports it lazily; skip if extra absent

from grim.adapter import launcher  # noqa: E402


def test_config_path_resolves_to_the_packaged_yaml() -> None:
    path = launcher._config_path()
    assert path.endswith("grimoire.yaml")
    assert Path(path).is_file()


def test_trajectory_path_honors_env_and_is_unique_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_TRAJ_DIR", "/var/tmp")
    path = launcher._trajectory_path()
    assert path.startswith("/var/tmp/grimoire-")
    assert path.endswith(".traj.json")


def test_build_mini_args_unattended_forces_yolo_and_exit_immediately() -> None:
    # interactive defaults False -> the unattended path
    spec = launcher.LaunchSpec("/cfg/grimoire.yaml", "/tmp/x.traj.json")
    args = launcher.build_mini_args([], spec)
    # our config is prepended, an unattended run is always yolo + exit-immediately,
    # and the injected trajectory lands because the user gave no -o.
    assert args[:4] == ["-c", "/cfg/grimoire.yaml", "-y", "--exit-immediately"]
    assert args[-2:] == ["-o", "/tmp/x.traj.json"]


def test_build_mini_args_attended_keeps_the_finish_prompt() -> None:
    spec = launcher.LaunchSpec("/cfg.yaml", "/t.traj.json", interactive=True)
    args = launcher.build_mini_args([], spec)
    # attended terminal: still yolo, but --exit-immediately is withheld so mini's
    # post-submit "type a new task" prompt survives (the session can continue).
    assert "-y" in args
    assert "--exit-immediately" not in args


def test_build_mini_args_promotes_a_bare_positional_to_task() -> None:
    spec = launcher.LaunchSpec("/cfg.yaml", "/t.traj.json")
    args = launcher.build_mini_args(["do the thing", "-m", "x"], spec)
    assert "-t" in args and "do the thing" in args
    assert args[args.index("-t") + 1] == "do the thing"


def test_build_mini_args_forwards_flags_and_respects_user_output() -> None:
    spec = launcher.LaunchSpec("/c.yaml", "/auto.traj.json")
    args = launcher.build_mini_args(["-t", "q", "-o", "/mine.json"], spec)
    # user's own -o wins: the auto trajectory is not appended.
    assert "/auto.traj.json" not in args
    assert args.count("-o") == 1 and "/mine.json" in args


def test_build_mini_args_injects_model_default_when_absent() -> None:
    spec = launcher.LaunchSpec("/c.yaml", "/t.traj.json", model_default="prov/model")
    args = launcher.build_mini_args(["do X"], spec)
    assert args[args.index("-m") + 1] == "prov/model"


def test_build_mini_args_explicit_model_beats_the_default() -> None:
    spec = launcher.LaunchSpec("/c.yaml", "/t.traj.json", model_default="prov/default")
    args = launcher.build_mini_args(["-t", "q", "-m", "chosen/one"], spec)
    # user's -m wins: the env default is not appended a second time.
    assert args.count("-m") == 1 and "prov/default" not in args


def test_main_reads_grim_model_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setenv("GRIM_MODEL", "env/model")
    captured: dict[str, object] = {}
    import minisweagent.run.mini as minirun

    monkeypatch.setattr(minirun, "app", lambda args, standalone_mode: captured.update(args=args))

    assert launcher.main(["a task"]) == 0  # no -m passed; env supplies it
    forwarded = captured["args"]
    assert isinstance(forwarded, list)
    assert forwarded[forwarded.index("-m") + 1] == "env/model"


def test_main_inits_the_library_then_invokes_mini_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    # Force a non-tty stdin so the unattended path is deterministic under
    # `pytest -s` or a CI terminal, not dependent on ambient isatty().
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    captured: dict[str, object] = {}

    # Stub mini's app so the loop never runs; main imports it lazily by name,
    # so patching the module attribute is picked up at call time.
    import minisweagent.run.mini as minirun

    def fake_app(args: list[str], standalone_mode: bool) -> None:
        captured["args"] = args
        captured["standalone_mode"] = standalone_mode

    monkeypatch.setattr(minirun, "app", fake_app)

    rc = launcher.main(["hello task", "-m", "test/model"])

    assert rc == 0
    assert captured["standalone_mode"] is False
    forwarded = captured["args"]
    assert isinstance(forwarded, list)
    assert "-y" in forwarded and "--exit-immediately" in forwarded
    assert forwarded[forwarded.index("-t") + 1] == "hello task"
    # init ran: the DB now exists and holds the seeded library.
    assert (tmp_path / "grimoire.db").exists()


def test_main_attended_terminal_keeps_the_finish_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))

    # A tty stdin makes main() choose the attended path; --exit-immediately is
    # then withheld so mini keeps its post-submit "type a new task" prompt.
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    captured: dict[str, object] = {}
    import minisweagent.run.mini as minirun

    monkeypatch.setattr(minirun, "app", lambda args, standalone_mode: captured.update(args=args))

    assert launcher.main(["a task", "-m", "x/y"]) == 0
    forwarded = captured["args"]
    assert isinstance(forwarded, list)
    assert "-y" in forwarded and "--exit-immediately" not in forwarded


def test_main_help_prints_grim_screen_without_init_or_mini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    # If --help fell through to init, this DB would be created; it must not be.
    import minisweagent.run.mini as minirun

    def boom(**_: object) -> None:  # mini must never be invoked for grim --help
        raise AssertionError("mini app should not run for --help")

    monkeypatch.setattr(minirun, "app", boom)

    assert launcher.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "grim-agent" in out and "six verbs" in out
    assert "--mini-help" in out  # pointer to the underlying option list
    assert not (tmp_path / "grimoire.db").exists()  # no DB init on help


def test_main_mini_help_delegates_without_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    captured: dict[str, object] = {}
    import minisweagent.run.mini as minirun

    def fake_app(args: list[str], standalone_mode: bool) -> None:
        captured["args"] = args

    monkeypatch.setattr(minirun, "app", fake_app)

    assert launcher.main(["--mini-help"]) == 0
    assert captured["args"] == ["--help"]  # forwarded to mini's own help
    assert not (tmp_path / "grimoire.db").exists()  # still no DB init


def test_main_silences_mini_startup_banner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.delenv("MSWEA_SILENT_STARTUP", raising=False)
    import minisweagent.run.mini as minirun

    monkeypatch.setattr(minirun, "app", lambda args, standalone_mode: None)

    assert launcher.main(["a task", "-m", "x/y"]) == 0
    # mini's import-time banner is guarded by this env var; main forces it on.
    import os

    assert os.environ["MSWEA_SILENT_STARTUP"] == "1"


def test_main_reports_missing_agent_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a core-only install: block the mini import with a real
    # ModuleNotFoundError via a meta-path finder, so main() must return the
    # friendly nonzero code instead of crashing with a traceback.
    class _Block:
        def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
            if fullname == "minisweagent.run.mini":
                raise ModuleNotFoundError(fullname)
            # falling off the end returns None -> defer to the next finder

    monkeypatch.delitem(sys.modules, "minisweagent.run.mini", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Block(), *sys.meta_path])

    assert launcher.main(["anything"]) == launcher.MISSING_EXTRA_EXIT_CODE


def test_build_mini_args_injects_cost_and_step_defaults() -> None:
    spec = launcher.LaunchSpec(
        config="/c.yaml", trajectory="/t.traj.json", cost_default="5", step_default="10"
    )
    args = launcher.build_mini_args(["do X"], spec)
    assert args[args.index("-l") + 1] == "5"
    assert "agent.step_limit=10" in args


def test_build_mini_args_explicit_cost_beats_default() -> None:
    spec = launcher.LaunchSpec(config="/c.yaml", trajectory="/t.traj.json", cost_default="9")
    args = launcher.build_mini_args(["-t", "q", "-l", "2"], spec)
    assert args.count("-l") == 1 and "9" not in args  # user's -l wins


def test_build_mini_args_omits_cost_and_step_when_unset() -> None:
    spec = launcher.LaunchSpec(config="/c.yaml", trajectory="/t.traj.json")
    args = launcher.build_mini_args(["do X"], spec)
    assert "-l" not in args
    assert not any(a.startswith("agent.step_limit=") for a in args)


# --- print mode (-p / --output-format) --------------------------------------


def test_parse_print_options_absent_leaves_argv_untouched() -> None:
    opts, rest = launcher.parse_print_options(["-t", "task", "-m", "x"])
    assert opts.enabled is False
    assert opts.output_format is launcher.OutputFormat.TEXT
    assert rest == ["-t", "task", "-m", "x"]  # nothing stripped for mini


def test_parse_print_options_strips_p_and_keeps_the_rest() -> None:
    for flag in ("-p", "--print"):
        opts, rest = launcher.parse_print_options([flag, "-t", "task"])
        assert opts.enabled is True
        assert rest == ["-t", "task"]  # the print flag never forwards to mini


def test_parse_print_options_output_format_implies_print_and_is_stripped() -> None:
    opts, rest = launcher.parse_print_options(["-t", "q", "--output-format", "json"])
    assert opts.enabled is True  # --output-format implies -p
    assert opts.output_format is launcher.OutputFormat.JSON
    assert rest == ["-t", "q"]  # both the flag and its value are consumed


def test_parse_print_options_accepts_joined_output_format() -> None:
    opts, rest = launcher.parse_print_options(["--output-format=text", "-t", "q"])
    assert opts.output_format is launcher.OutputFormat.TEXT
    assert rest == ["-t", "q"]


def test_parse_print_options_rejects_a_bad_format_value() -> None:
    with pytest.raises(SystemExit) as exc:
        launcher.parse_print_options(["--output-format", "yaml"])
    assert exc.value.code == _USAGE_EXIT  # usage error, not a crash


def test_parse_print_options_rejects_a_missing_format_value() -> None:
    with pytest.raises(SystemExit) as exc:
        launcher.parse_print_options(["-t", "q", "--output-format"])
    assert exc.value.code == _USAGE_EXIT


_USAGE_EXIT = 2  # SystemExit code the launcher uses for usage errors
_STUB_COST = 0.01
_STUB_CALLS = 3


def _submitted_traj(result: str) -> dict[str, object]:
    return {
        "info": {
            "exit_status": "Submitted",
            "submission": result,
            "model_stats": {"instance_cost": _STUB_COST, "api_calls": _STUB_CALLS},
        }
    }


def test_parse_result_returns_the_submission_on_a_clean_finish() -> None:
    assert launcher.parse_result(_submitted_traj("6765")) == "6765"


def test_parse_result_is_none_when_the_agent_never_submitted() -> None:
    limited = {"info": {"exit_status": "LimitsExceeded", "submission": ""}}
    assert launcher.parse_result(limited) is None
    assert launcher.parse_result({}) is None  # no info block at all


def test_summarize_run_pulls_result_cost_and_calls() -> None:
    summary = launcher.summarize_run(_submitted_traj("42"), "/tmp/x.traj.json")
    assert summary.result == "42"
    assert summary.exit_status == "Submitted"
    assert summary.cost == _STUB_COST
    assert summary.api_calls == _STUB_CALLS
    assert summary.trajectory == "/tmp/x.traj.json"


def test_format_output_text_is_the_bare_answer() -> None:
    summary = launcher.summarize_run(_submitted_traj("6765"), "/t.json")
    assert launcher.format_output(summary, launcher.OutputFormat.TEXT) == "6765"


def test_format_output_text_is_empty_when_no_answer() -> None:
    summary = launcher.summarize_run({}, "/t.json")
    assert launcher.format_output(summary, launcher.OutputFormat.TEXT) == ""


def test_format_output_json_round_trips_with_expected_keys() -> None:
    import json

    summary = launcher.summarize_run(_submitted_traj("6765"), "/t.json")
    payload = json.loads(launcher.format_output(summary, launcher.OutputFormat.JSON))
    assert payload["result"] == "6765"
    assert payload["exit_status"] == "Submitted"
    assert payload == {
        "result": "6765",
        "exit_status": "Submitted",
        "cost": _STUB_COST,
        "api_calls": _STUB_CALLS,
        "trajectory": "/t.json",
    }


def test_format_output_json_result_is_null_on_no_answer() -> None:
    import json

    summary = launcher.summarize_run({}, "/t.json")
    payload = json.loads(launcher.format_output(summary, launcher.OutputFormat.JSON))
    assert payload["result"] is None


def test_main_print_mode_emits_only_the_answer_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setenv("GRIM_TRAJ_DIR", str(tmp_path))
    import minisweagent.run.mini as minirun

    # A stub mini that "runs" by printing UI to stdout and writing a trajectory
    # with a clean submission — exactly the shape run_print must handle.
    def fake_app(args: list[str], standalone_mode: bool) -> None:
        print("=== noisy mini UI that must NOT reach real stdout ===")
        traj = args[args.index("-o") + 1]
        Path(traj).write_text(
            '{"info": {"exit_status": "Submitted", "submission": "6765",'
            ' "model_stats": {"instance_cost": 0.0, "api_calls": 2}}}'
        )

    monkeypatch.setattr(minirun, "app", fake_app)

    rc = launcher.main(["-p", "compute fib(20)", "-m", "test/model"])
    out = capsys.readouterr().out

    assert rc == 0
    assert out.strip() == "6765"  # ONLY the answer — mini's UI went to stderr
    assert "noisy mini UI" not in out


# --- --continue: recall + session lineage -----------------------------------


def test_take_flag_strips_and_reports_presence() -> None:
    present, rest = launcher._take_flag(["--continue", "-t", "q"], "--continue")
    assert present is True
    assert rest == ["-t", "q"]  # flag removed, never forwarded to mini
    absent, rest2 = launcher._take_flag(["-t", "q"], "--continue")
    assert absent is False
    assert rest2 == ["-t", "q"]


def test_build_mini_args_injects_session_id_when_set() -> None:
    spec = launcher.LaunchSpec("/c.yaml", "/t.traj.json", session_id="sess-123")
    args = launcher.build_mini_args(["do X"], spec)
    assert "environment.session_id=sess-123" in args


def test_build_mini_args_omits_session_id_when_absent() -> None:
    spec = launcher.LaunchSpec("/c.yaml", "/t.traj.json")
    args = launcher.build_mini_args(["do X"], spec)
    assert not any(a.startswith("environment.session_id=") for a in args)


def test_last_agent_session_id_returns_most_recent_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect()
    db.migrate(conn)
    conn.executemany(
        "INSERT INTO session (id, kind, started_at) VALUES (?, ?, ?)",
        [
            ("old", "agent", "2026-08-01 00:00:00"),
            ("new", "agent", "2026-08-03 00:00:00"),
            ("human_newer", "human", "2026-08-05 00:00:00"),  # newer, but not an agent
        ],
    )
    conn.commit()
    conn.close()
    assert launcher.last_agent_session_id() == "new"


def test_last_agent_session_id_none_on_fresh_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    assert launcher.last_agent_session_id() is None


def test_main_continue_enables_recall_and_reuses_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # non-tty -> deterministic path
    monkeypatch.delenv("GRIM_RECALL", raising=False)  # registered -> cleaned up on teardown
    # A prior agent session for --continue to reuse. Far-future timestamp so it
    # is unambiguously the most-recent agent session, whatever init records.
    conn = db.connect()
    db.migrate(conn)
    conn.execute(
        "INSERT INTO session (id, kind, started_at) VALUES ('prev', 'agent', '2099-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    captured: dict[str, object] = {}
    import minisweagent.run.mini as minirun

    monkeypatch.setattr(minirun, "app", lambda args, standalone_mode: captured.update(args=args))

    rc = launcher.main(["--continue", "a task", "-m", "x/y"])

    assert rc == 0
    import os

    assert os.environ.get("GRIM_RECALL") == "1"  # recall turned on
    forwarded = captured["args"]
    assert isinstance(forwarded, list)
    assert "--continue" not in forwarded  # grim-only flag, never sent to mini
    assert "environment.session_id=prev" in forwarded  # lineage reused
