# Vendored eval fixtures — provenance

Fetched 2026-08-04. These files are third-party benchmark data committed
verbatim (bugs, formatting and all) — they are excluded from ruff/mypy on
purpose (`pyproject.toml [tool.ruff] extend-exclude`). Do not "fix" them
outside an eval run; the bugs are the tasks.

## QuixBugs — MIT (see licenses/QUIXBUGS-LICENSE + QUIXBUGS-legal_notes.txt)

Source: https://github.com/jkoppel/QuixBugs (master @ fetch date).
Each program contains exactly one planted single-line defect.

| task | vendored verbatim | adapted |
|---|---|---|
| quixbugs-quicksort | `python_programs/quicksort.py`, `json_testcases/quicksort.json` | `run_tests.py` (ours: stdlib driver over the JSON cases) |
| quixbugs-bfs | `python_programs/breadth_first_search.py`, `python_testcases/node.py` | `run_tests.py` (upstream's five pytest cases, rewritten pytest-free; a crash counts as a failing case) |
| quixbugs-sqrt | `python_programs/sqrt.py`, `json_testcases/sqrt.json` | `run_tests.py` (ours: grades the docstring contract `abs(result - sqrt(x)) <= epsilon` instead of upstream's exact Newton float, so any correct fix passes) |
| quixbugs-lis | `python_programs/lis.py`, `json_testcases/lis.json` | `run_tests.py` (ours: stdlib driver over the JSON cases) |

## HumanEvalFix — MIT (bigcode/humanevalpack)

Source: https://huggingface.co/datasets/bigcode/humanevalpack (config
`python`, split `test`, via the HF datasets-server rows API). Each
`<entry_point>.py` is the dataset's `prompt` (signature + docstring) plus
`buggy_solution`, verbatim; each `run_tests.py` embeds the dataset's `test`
field (the `check()` asserts) verbatim under an import line.

| task | dataset row | bug type | symptom |
|---|---|---|---|
| hef-py0 | Python/0 `has_close_elements` | missing logic | incorrect output |
| hef-py1 | Python/1 `separate_paren_groups` | operator misuse | incorrect output |
| hef-py76 | Python/76 `is_simple_power` | variable misuse | infinite loop |
