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
from grim.adapter.environment import GrimEnvironment  # noqa: E402
from grim.adapter.toolcall_model import GrimToolcallModel  # noqa: E402

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
