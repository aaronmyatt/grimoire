"""Runtime tests for agent_compare in seeds/bodies_compare.py (docs/delegation-plan.md,
Phase 3b), dispatched as `grim run` would. Each delegate is a fixture job folder:
result.md, status.json (with agent_done's sha) and meta.json. No model runs."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from grim.exec.dispatch import ExecutionRequest, ExecutionResult, ScriptVersion, dispatch
from grim.seeds.bodies import SEEDS

_TIMEOUT_S = 30.0
_USAGE_EXIT_CODE, _ONE_SOURCE_EXIT_CODE, _NO_SOURCE_EXIT_CODE = 2, 3, 1
_MAX_PACKET_BYTES, _MAX_RESULT_BYTES = 4096, 16384
_MAX_LINE_CHARS = 170  # "A#12 + " and a claim cut to 160 chars
_CLAIMS_A = (
    "The wait hook fires on pane death — evidence: app.py:12",
    "Stamps are written atomically by rename — evidence: app.py:3",
)
_CLAIMS_B = (
    "the wait hook fires when the pane dies — evidence: app.py:12",
    "Retries are capped at three — evidence: `grep -c retry app.py` → 3",
)


def _run(argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == "agent_compare")
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _answer(verdict: str, claims: tuple[str, ...], drop: str = "") -> str:
    numbered = "".join(f"{n}. {claim}\n" for n, claim in enumerate(claims, 1))
    parts = {
        "## Verdict": verdict,
        "## Answer": "Read the code, then checked it.",
        "## Claims": numbered,
        "## Confidence": "high — read twice",
        "## Open": "nothing",
    }
    return "".join(f"{head}\n{text}\n\n" for head, text in parts.items() if head != drop)


def _job(tmp_path: Path, job: str, answer: str = "", state: str = "DONE") -> None:
    """A finished delegate: what agent_done or agent_exec leave in its run folder."""
    folder = tmp_path / "run" / job
    folder.mkdir(parents=True)
    (folder / "result.md").write_text(answer)
    digest = hashlib.sha256(answer.encode()).hexdigest()
    record = {"job": job, "state": state, "stamped_at": 1, "sha256": digest}
    (folder / "status.json").write_text(json.dumps(record))
    meta = {"wall_s": 842.0, "cost_usd": 0.83, "dir": str(tmp_path / "repo")}
    (folder / "meta.json").write_text(json.dumps(meta))


@pytest.fixture(autouse=True)
def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "app.py").write_text("".join(f"line {n}\n" for n in range(20)))


def test_agent_compare_two_answers_that_agree(tmp_path: Path) -> None:
    _job(tmp_path, "q-claude", _answer("Stamp completion before anything else.", _CLAIMS_A))
    _job(tmp_path, "q-codex", _answer("Stamp the completion before anything else.", _CLAIMS_B))
    result = _run(["q-claude", "q-codex"])
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == (
        "q-claude DONE 14m02s $0.83 verified 2/2 · q-codex DONE 14m02s $0.83 verified 1/2"
    )
    assert lines[1].endswith("→ AGREE")
    assert lines[2] == "agreed (1) · only-A (1) · only-B (1)"
    assert "A#2 + Stamps are written atomically by rename" in result.stdout
    assert "B#2 ? Retries are capped at three" in result.stdout


def test_agent_compare_two_answers_that_differ(tmp_path: Path) -> None:
    _job(tmp_path, "q-claude", _answer("The race is in the wait hook.", _CLAIMS_A))
    _job(tmp_path, "q-codex", _answer("Retries cause it; raise the cap.", _CLAIMS_B))
    result = _run(["q-claude", "q-codex"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[1].endswith("→ DIFFER")


@pytest.mark.parametrize("other", ["malformed", "auth"])
def test_agent_compare_with_one_good_answer_is_a_single_source(tmp_path: Path, other: str) -> None:
    _job(tmp_path, "q-claude", _answer("Stamp completion first.", _CLAIMS_A))
    if other == "malformed":  # status says DONE, but a heading is missing
        _job(tmp_path, "q-codex", _answer("Stamp it.", _CLAIMS_B, drop="## Open"))
    else:
        _job(tmp_path, "q-codex", state="AUTH")
    result = _run(["q-claude", "q-codex"])
    assert result.exit_code == _ONE_SOURCE_EXIT_CODE
    assert f"q-codex {other.upper()} 14m02s $0.83" in result.stdout.splitlines()[0]
    assert (
        result.stdout.splitlines()[1] == 'single source q-claude: verdict "Stamp completion first."'
    )


def test_agent_compare_with_no_good_answer_exits_1(tmp_path: Path) -> None:
    _job(tmp_path, "q-claude", state="TIMEOUT")
    result = _run(["q-claude", "q-codex"])  # q-codex has no folder at all
    assert result.exit_code == _NO_SOURCE_EXIT_CODE
    assert result.stdout.startswith("q-claude TIMEOUT 14m02s $0.83 · q-codex NO STATUS cost=null")


def test_agent_compare_checks_path_line_evidence_and_never_runs_a_command(tmp_path: Path) -> None:
    witness = tmp_path / "ran"
    claims = (
        "Line twelve exists — evidence: app.py:12",
        "Line ninety-nine exists — evidence: app.py:99",
        "A file outside the folder — evidence: ../outside.py:1",
        f"A command is recorded — evidence: `touch {witness}`",
    )
    _job(tmp_path, "q-claude", _answer("Mixed evidence.", claims))
    _job(tmp_path, "q-codex", _answer("Unrelated other view.", ("Nothing in common here",)))
    (tmp_path / "outside.py").write_text("x\n")
    result = _run(["q-claude", "q-codex"])
    assert result.exit_code == 0, result.stderr
    marks = [line.split()[1] for line in result.stdout.splitlines() if line.startswith("A#")]
    assert marks == ["+", "x", "x", "?"]
    assert not witness.exists()


def test_agent_compare_packet_stays_under_4096_bytes_for_two_16_kib_results(
    tmp_path: Path,
) -> None:
    for job, word in (("q-claude", "alpha"), ("q-codex", "omega")):
        shared = [f"shared claim {n} " + "with a long reason " * 12 for n in range(10)]
        own = [" ".join(f"{word}{n}w{k}" for k in range(30)) for n in range(40)]  # no overlap
        claims = tuple(shared + own)  # so agreed, only-A and only-B all fill their 8 lines
        answer = _answer(f"{word} " * 60, claims)
        padding = "y" * (_MAX_RESULT_BYTES - len(answer.encode()))  # fill to exactly 16 KiB
        answer = answer.replace("## Answer\n", f"## Answer\n{padding}", 1)
        assert len(answer.encode()) == _MAX_RESULT_BYTES
        _job(tmp_path, job, answer)
    result = _run(["q-claude", "q-codex"])
    assert result.exit_code == 0, result.stderr
    assert len(result.stdout.encode()) <= _MAX_PACKET_BYTES
    assert result.stdout.endswith("\n…\n")  # it was cut, and says so
    assert all(len(line) <= _MAX_LINE_CHARS for line in result.stdout.splitlines()[3:])


@pytest.mark.parametrize("argv", [[], ["a"], ["a", "a"], ["a", "b c"], ["a", "b", "c"]])
def test_agent_compare_wrong_calls_exit_2(argv: list[str]) -> None:
    result = _run(argv)
    assert result.exit_code == _USAGE_EXIT_CODE
    assert "agent_compare" in result.stderr


# --- agent_stats (delegation plan 6c): the scoreboard of past asks ---------------------------
_DAY_S, _USAGE_EXIT_CODE = 86400, 2


def _stats(argv: list[str]) -> ExecutionResult:
    body = next(s.body for s in SEEDS if s.name == "agent_stats")
    request = ExecutionRequest(argv=argv, stdin=None, cwd=None, timeout=_TIMEOUT_S)
    return dispatch(ScriptVersion(language="python", body=body), request)


def _ask(run: Path, job: str, state: str = "", raw: str = "", **meta: object) -> Path:
    folder = run / job
    folder.mkdir(parents=True)
    (folder / "brief.md").write_text("the question")
    if state or raw:
        (folder / "status.json").write_text(raw or json.dumps({"job": job, "state": state}))
    if meta:
        (folder / "meta.json").write_text(json.dumps({"agent": job.split("-")[-1], **meta}))
    return folder


def test_agent_stats_scores_each_agent_from_status_and_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixture job dirs -> the expected table; --help states the 30-day default."""
    run = tmp_path / "run"  # GRIM_RUN_DIR, set by the autouse fixture above
    _ask(run, "a1-claude", "DONE", wall_s=120, cost_usd=0.5)
    _ask(run, "a2-claude", "DONE", wall_s=61.4, cost_usd=0.3)
    _ask(run, "a3-claude", "TIMEOUT", wall_s=900, cost_usd=0)  # a zero cost is no cost
    _ask(run, "a1-codex", "DONE", cost_usd=None)
    _ask(run, "a2-codex", exit=0)  # never stamped: a failure state, not a crash
    _ask(run, "a3-codex", raw="not json")
    _ask(run, "tui-pi", "DONE")  # no meta.json: the agent comes from the job name
    stale = time.time() - 40 * _DAY_S
    os.utime(_ask(run, "old-claude", "AUTH", wall_s=5) / "status.json", (stale, stale))
    (run / "notes").mkdir()  # no brief, status, meta or result: not a delegate's dir
    (run / "x.log").write_text("a job log")
    result = _stats([])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"agent_stats: 7 asks in the last 30 days in {run}",
        "AGENT    ASKS DONE DONE%    WALL  COST           FAILURES",
        "claude      3    2   66%   2m00s  $0.4000 (2)    TIMEOUT=1",
        "codex       3    1   33% unknown  unknown        bad-status=1 no-status=1",
        "pi          1    1  100% unknown  unknown        -",
    ]
    wider = _stats(["60"]).stdout.splitlines()
    assert wider[2].startswith("claude      4    2   50%") and wider[2].endswith("AUTH=1 TIMEOUT=1")
    helped, refused = _stats(["--help"]), _stats(["0"])
    assert helped.exit_code == 0 and "default: 30" in helped.stdout
    assert refused.exit_code == _USAGE_EXIT_CODE and "DAYS is a number above 0" in refused.stderr
    monkeypatch.setenv("GRIM_RUN_DIR", str(tmp_path / "missing"))
    assert "0 asks in the last 30 days" in _stats([]).stdout
