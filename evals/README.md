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

Infra can also be exercised end-to-end without a model: point GRIM_AGENT_CMD
at any executable that works in the Run workspace and prints an answer, e.g.

    GRIM_AGENT_CMD=/path/to/stub smevals run evals/grim-debug -t quixbugs-quicksort -m fake -g
