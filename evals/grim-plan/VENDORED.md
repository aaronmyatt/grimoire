# Vendored eval fixtures — provenance

Fetched 2026-08-04. Unlike grim-debug/grim-solve there is no `tasks/files/`
data here — every vendored artifact lives inside the task yamls themselves
(prompts, goldens, init/goal facts), extracted from the upstream datasets by a
one-shot vendoring script (the full datasets are 6–24 MB and are NOT
committed).

## NaturalPlan — code Apache-2.0, data CC-BY-4.0 (see licenses/NATURALPLAN-LICENSE)

Source: https://github.com/google-deepmind/natural-plan
(`data/trip_planning.json`, `data/calendar_scheduling.json`). Task yaml
`source_id` records each upstream example key.

Adaptations, on purpose:
- Prompts use the dataset's `prompt_0shot` (5-shot bloats an agent harness),
  with a fixed output-format instruction appended so 0-shot answers parse.
  Scores are therefore NOT comparable to the paper's 5-shot numbers.
- The graders in `checkers/plan-grade` (`parse_trip_response`,
  `parse_calendar_response` + comparison semantics) are adapted near-verbatim
  from the repo's `evaluate_trip_planning.py` / `evaluate_calendar_scheduling.py`
  (Apache-2.0), stripped of absl.
- Trip tasks carry the golden as the upstream `cities`/`durations` fields;
  calendar tasks carry `golden_plan` verbatim.

## PlanBench — MIT (see licenses/PLANBENCH-LICENSE)

Source: https://github.com/karthikv792/LLMs-Planning
(`plan-bench/prompts/{blocksworld,mystery_blocksworld}/task_1_plan_generation.json`).
`source_id` records the upstream `instance_id`.

- Prompts are the upstream `query` verbatim (domain rules + one worked
  example + the problem, ending at `[PLAN]`).
- `init`/`goal` fact lists were machine-parsed from the query's templated
  NL statements (the raw PDDL uses different object names than the plans, so
  the NL is the ground truth here) and cross-validated: every upstream
  `ground_truth_plan` executes to its goal under `checkers/plan-grade`'s
  STRIPS tables, in both s-expression and PlanBench's natural-language plan
  formats, and corrupted/truncated plans fail (43/43 oracle checks).
- Grading is plan VALIDATION against the domain (transcribed from the
  upstream `generated_domain.pddl` files) — any valid plan passes, not just
  the golden.
