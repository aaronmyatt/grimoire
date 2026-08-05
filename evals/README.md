# Grimoire evals

Evaluation harnesses for the grim-agent, built on
[smevals](https://github.com/prime-radiant-inc/smevals). Install it once:

    uv tool install smevals        # or run ad hoc: uvx smevals ...

## Layout

- `grim-smoke/` — a deterministic, judge-free smoke eval that proves the
  Runner drives `grim-agent` end-to-end (Phase 8 scaffolding).
- `grim-debug/` — debugging: cherry-picked single-bug programs from QuixBugs
  and HumanEvalFix (see its `VENDORED.md`). The agent gets a workspace copy
  of the buggy file plus a runnable test driver; the grader restores pristine
  test files and re-runs the driver, so only a real fix passes.
- `grim-solve/` — problem-solving: cherry-picked ARC-AGI-1 training puzzles
  (see its `VENDORED.md`). The agent explores `task.json` in its workspace
  and answers with the output grid; graded by exact structural match.
- `grim-plan/` — planning: cherry-picked NaturalPlan trip/calendar tasks and
  PlanBench Blocksworld + Mystery Blocksworld instances (see its
  `VENDORED.md`). NaturalPlan grades via the suite's own parsers; blocksworld
  grades by STRIPS plan validation — any valid plan passes. This is the
  model-separation suite (Mystery Blocksworld and the 10-city trip fail even
  frontier models) and the sharpest warm-vs-cold probe: blocksworld tasks are
  ordered so a solver banked on the easy instance pays off on the hard ones.

Tasks in each eval are ordered easy → hard on published pass-rate evidence,
so cross-model deltas are visible without large run counts. Vendored fixture
data under `tasks/files/` is committed verbatim and excluded from lint — the
bugs ARE the tasks.

## Warm vs cold library — the compounding variable

`grim-smoke/run-grim` always sets `GRIM_DB` explicitly, so an eval **never**
touches your real `~/.grimoire`. Which DB it points at is the experiment's knob:

| Mode | How | What it tests |
|------|-----|---------------|
| **Cold** (default) | export nothing | Fresh DB per Run — the control; every task starts from a blank library. |
| **Warm** | `export GRIM_EVAL_DB=/tmp/grim-eval.db` | One shared library accumulates across tasks — the compounding condition. |
| **Grouped** | task key `grim_db: <path>` | Per-task DB path, e.g. one DB per repo (SWE-bench, next task). |

## Language modes

`run-grim` pins `GRIM_LANGUAGES` the same way it pins `GRIM_DB`, so the set
of languages the agent may write never leaks from the operator's
`~/.grimoire/config.toml`. The default is the baseline pair `python,bash`
(both are always in the agent's schema anyway); a task opts into more by
declaring a `grim_languages:` key (smevals uppercases it to
`SMEVALS_TASK_GRIM_LANGUAGES` for the runner, and it lands in `run.yaml`),
e.g. `grim_languages: python,bash,jq`.

This is the knob for language-variation evals: run the same task with
different `grim_languages:` sets (or omit it for the baseline) and compare
pass rates and the language lean of agent-written scripts across models.


`grim-agent` must be on `PATH` (`uv tool install "grimoire[agent]"`), or set
`GRIM_AGENT_CMD` to launch it another way, e.g.
`export GRIM_AGENT_CMD="uv run --project $PWD grim-agent"`.

## Run it

    # cold (control): fresh library each task
    smevals run evals/grim-smoke -m anthropic/claude-sonnet-4-5 -g

    # warm: shared library — watch fib-large reuse the script fib-small wrote
    rm -f /tmp/grim-eval.db
    export GRIM_EVAL_DB=/tmp/grim-eval.db
    smevals run evals/grim-smoke -m anthropic/claude-sonnet-4-5 -g

    smevals report evals/grim-smoke     # terminal report
    smevals serve  evals/grim-smoke     # live web UI

## Checker self-tests (no API cost)

    ./evals/grim-smoke/checkers/answer-equals.selftest.sh
    ./evals/grim-debug/checkers/run-tests.selftest.sh
    ./evals/grim-solve/checkers/json-grid-equals.selftest.sh
    ./evals/grim-plan/checkers/plan-grade.selftest.sh

Infra can also be exercised end-to-end without a model: point GRIM_AGENT_CMD
at any executable that works in the Run workspace and prints an answer, e.g.

    GRIM_AGENT_CMD=/path/to/stub smevals run evals/grim-debug -t quixbugs-quicksort -m fake -g
