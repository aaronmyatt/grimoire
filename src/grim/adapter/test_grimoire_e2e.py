"""Offline proof of Phase 2's done-when: DefaultAgent + GrimEnvironment
solving a toy task using only grim verbs, plus an injected `ls -la`
producing the protocol reminder instead of running as shell. No live
LLM — mini-swe-agent's DeterministicModel replays a scripted transcript.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("minisweagent")  # adapter/ needs the optional `adapter` extra

import yaml  # type: ignore[import-untyped]  # noqa: E402 -- no stubs shipped for PyYAML
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.environments import get_environment_class  # noqa: E402
from minisweagent.models import get_model_class  # noqa: E402
from minisweagent.models.test_models import DeterministicModel, make_output  # noqa: E402

from grim import db  # noqa: E402
from grim.adapter.agent import GrimAgent  # noqa: E402
from grim.adapter.environment import GrimEnvironment  # noqa: E402
from grim.adapter.toolcall_model import GrimToolcallModel  # noqa: E402
from grim.adapter.tools import render_command  # noqa: E402

GRIMOIRE_YAML = Path(__file__).parent / "grimoire.yaml"


@pytest.fixture(autouse=True)
def _grim_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_DB", str(tmp_path / "grimoire.db"))


def test_grimoire_yaml_resolves_the_grim_environment_class() -> None:
    config = yaml.safe_load(GRIMOIRE_YAML.read_text())
    spec = config["environment"]["environment_class"]
    assert get_environment_class(spec) is GrimEnvironment


def test_grimoire_yaml_resolves_the_toolcall_model_class() -> None:
    config = yaml.safe_load(GRIMOIRE_YAML.read_text())
    spec = config["model"]["model_class"]
    assert get_model_class("placeholder-model", spec) is GrimToolcallModel


def test_toy_task_end_to_end_via_tool_calls() -> None:
    # DefaultAgent + GrimEnvironment solving a toy task through structured
    # tool-call actions, finishing with the submit tool (the deterministic
    # stop). No fenced-block parsing, no sentinel.
    db.migrate(db.connect())
    outputs = [
        make_output(
            "writing the greeting script",
            [
                {
                    "tool": "write",
                    "args": {
                        "name": "greet",
                        "lang": "bash",
                        "desc": "prints a greeting",
                        "body": "echo hi",
                    },
                }
            ],
        ),
        make_output("running it", [{"tool": "run", "args": {"name": "greet"}}]),
        make_output("submitting", [{"tool": "submit", "args": {"result": "said hi"}}]),
    ]
    model = DeterministicModel(outputs=outputs)  # type: ignore[no-untyped-call]
    env = GrimEnvironment(session_id="e2e-test")
    agent = DefaultAgent(
        model,
        env,
        system_template="You are a test agent.",
        instance_template="{{task}}",
        cost_limit=0,  # unlimited — the scripted transcript costs 1.0/call by default
    )

    result = agent.run("say hi")

    assert result["exit_status"] == "Submitted"
    assert result["submission"] == "said hi"  # the submit tool's result flows through
    ran = [m for m in agent.messages if "hi" in m.get("content", "")]
    assert ran, "the greet run's output should appear in an observation"


def test_grim_agent_runs_tool_actions_end_to_end() -> None:
    # Regression: the production agent is GrimAgent -> InteractiveAgent,
    # whose execute_actions reads action["command"] for every action (even
    # in yolo). Tool actions must carry that display field or the whole run
    # dies with KeyError('command') on the first step. (The DefaultAgent
    # test above does NOT exercise this path.)
    db.migrate(db.connect())

    def act(tool: str, args: dict[str, object]) -> dict[str, object]:
        # Shape actions exactly as GrimToolcallModel produces them, incl. the
        # `command` display field InteractiveAgent requires.
        return {
            "tool": tool,
            "args": args,
            "tool_call_id": "c",
            "command": render_command(tool, args),
        }

    outputs = [
        make_output(
            "write greet",
            [act("write", {"name": "greet", "lang": "bash", "desc": "greets", "body": "echo hi"})],
        ),
        make_output("run greet", [act("run", {"name": "greet"})]),
        make_output("finish", [act("submit", {"result": "said hi"})]),
    ]
    agent = GrimAgent(
        DeterministicModel(outputs=outputs),  # type: ignore[no-untyped-call]
        GrimEnvironment(session_id="grim-agent-e2e"),
        system_template="You are a test agent.",
        instance_template="{{task}}",
        mode="yolo",  # no per-action confirmation prompt (no stdin in tests)
        confirm_exit=False,  # submit ends the run without the "new task?" prompt
        cost_limit=0,
    )

    result = agent.run("say hi")

    assert result["exit_status"] == "Submitted"
    assert result["submission"] == "said hi"


def test_system_template_renders_operator_instructions() -> None:
    # The system_template must surface ~/.grimoire/system.md content when
    # grim_user_prompt is set, and render nothing when it's empty/absent.
    import jinja2  # ships transitively with mini-swe-agent

    template = yaml.safe_load(GRIMOIRE_YAML.read_text())["agent"]["system_template"]
    with_prompt = jinja2.Template(template).render(grim_user_prompt="ALWAYS use ripgrep.")
    assert "<operator_instructions>" in with_prompt
    assert "ALWAYS use ripgrep." in with_prompt

    without = jinja2.Template(template).render(grim_user_prompt="")
    assert "operator_instructions" not in without


def test_templates_render_enabled_languages() -> None:
    # The language-sweep confound fix: with grim_languages stashed, BOTH
    # templates must NAME the enabled set; without the var they fall back to
    # the old static python-or-bash text (grimoire.yaml's guard philosophy).
    import jinja2  # ships transitively with mini-swe-agent

    config = yaml.safe_load(GRIMOIRE_YAML.read_text())["agent"]
    enabled = ["python", "bash", "jq"]
    for name in ("system_template", "instance_template"):
        rendered = jinja2.Template(config[name]).render(grim_languages=enabled, task="t")
        assert "python, bash, jq" in rendered, name
        fallback = jinja2.Template(config[name]).render(task="t")
        assert "python or bash" in fallback, name
        assert "jq" not in fallback, name


def test_seed_list_reads_as_run_calls_and_documents_fallthrough() -> None:
    # The seed list must render each seed as a run() invocation with one
    # worked example (run() stays the canonical form), and the template must
    # document library fallthrough — since a5dc20d a bare script-name tool
    # call is a supported alias for run(name=...), not a FormatError, so the
    # prompt teaching "not callable directly" would contradict the adapter.
    template = yaml.safe_load(GRIMOIRE_YAML.read_text())["agent"]["system_template"]
    for seed in ("shell", "read_file", "write_file", "edit_file", "list_dir", "run_bg", "stop_bg"):
        assert f"run {seed}" in template, f"{seed} must be shown invoked via run"
    assert "falls through to run" in template
    assert 'run(name="read_file"' in template


def test_run_stashes_enabled_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    # grim_languages reaches the prompt from the SAME function that builds the
    # write/list schema enums (tools.lang_enum) — an enabled language becomes
    # visible prose, not just an enum entry the model may never read.
    monkeypatch.setenv("GRIM_LANGUAGES", "jq")
    db.migrate(db.connect())

    def act(tool: str, args: dict[str, object]) -> dict[str, object]:
        return {
            "tool": tool,
            "args": args,
            "tool_call_id": "c",
            "command": render_command(tool, args),
        }

    outputs = [make_output("done", [act("submit", {"result": "ok"})])]
    agent = GrimAgent(
        DeterministicModel(outputs=outputs),  # type: ignore[no-untyped-call]
        GrimEnvironment(session_id="langs-e2e"),
        system_template="langs: {{ grim_languages | join(', ') }}",
        instance_template="{{task}}",
        mode="yolo",
        confirm_exit=False,
        cost_limit=0,
    )

    result = agent.run("noop")

    assert result["exit_status"] == "Submitted"
    assert agent.extra_template_vars["grim_languages"] == ["bash", "jq", "python"]
    assert agent.messages[0]["content"] == "langs: bash, jq, python"
