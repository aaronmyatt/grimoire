# Vendored eval fixtures — provenance

Fetched 2026-08-10 via the HF datasets-server rows API by
`scripts/fetch-tasks` (stdlib-only; same approach as grim-debug's
HumanEvalFix vendoring). Task files under `tasks/` are **generated — do not
hand-edit**; regenerate or extend with e.g.:

    scripts/fetch-tasks --repo django/django --limit 10           # 001..010
    scripts/fetch-tasks --repo django/django --offset 10 --limit 10  # 011..020

## SWE-bench Verified

Source: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified
(split `test`, 500 rows) — the human-validated 500-instance subset of
SWE-bench (https://github.com/SWE-bench/SWE-bench, MIT-licensed code). The
dataset card carries no explicit license field at fetch date; problem
statements are public GitHub issue text from the underlying OSS projects.

Vendored per task, verbatim: `instance_id`, `repo`, `base_commit`,
`problem_statement` (inside the prompt), plus `created_at`/`difficulty` as a
provenance comment. **Deliberately not vendored:** `patch`, `test_patch`,
`hints_text`, `FAIL_TO_PASS`, `PASS_TO_PASS` — the first three would leak the
answer into the prompt's vicinity, and grading truth stays with the official
harness, which reads them from the dataset itself.

The initial committed set is the first 10 `django/django` instances in
`created_at` order — build plan Phase 8's per-repo chronological curriculum
(one shared library per repo; the compounding condition). Repos are cloned at
run time by `run-grim`; no repository code is vendored here.
