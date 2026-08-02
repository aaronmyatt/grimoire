"""Offline tests for the grim-agent launcher. The pure arg builder is tested
directly; main() is tested with mini's Typer app stubbed out, so no model is
queried and the agent loop never runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def test_build_mini_args_forces_unattended_config_and_trajectory() -> None:
    args = launcher.build_mini_args([], "/cfg/grimoire.yaml", "/tmp/x.traj.json")
    # our config is prepended, the run is always yolo + non-interactive, and
    # the injected trajectory lands because the user gave no -o.
    assert args[:4] == ["-c", "/cfg/grimoire.yaml", "-y", "--exit-immediately"]
    assert args[-2:] == ["-o", "/tmp/x.traj.json"]


def test_build_mini_args_promotes_a_bare_positional_to_task() -> None:
    args = launcher.build_mini_args(["do the thing", "-m", "x"], "/cfg.yaml", "/t.traj.json")
    assert "-t" in args and "do the thing" in args
    assert args[args.index("-t") + 1] == "do the thing"


def test_build_mini_args_forwards_flags_and_respects_user_output() -> None:
    args = launcher.build_mini_args(["-t", "q", "-o", "/mine.json"], "/c.yaml", "/auto.traj.json")
    # user's own -o wins: the auto trajectory is not appended.
    assert "/auto.traj.json" not in args
    assert args.count("-o") == 1 and "/mine.json" in args


def test_build_mini_args_injects_model_default_when_absent() -> None:
    args = launcher.build_mini_args(["do X"], "/c.yaml", "/t.traj.json", model_default="prov/model")
    assert args[args.index("-m") + 1] == "prov/model"


def test_build_mini_args_explicit_model_beats_the_default() -> None:
    args = launcher.build_mini_args(
        ["-t", "q", "-m", "chosen/one"], "/c.yaml", "/t.traj.json", model_default="prov/default"
    )
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
