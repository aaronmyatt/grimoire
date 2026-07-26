# Slice: adapter

## Purpose
Subclasses mini-swe-agent's environment so the model's action is parsed
and dispatched to `grim` in-process — the hard enforcement point for the
six-verb constraint (build plan D7, §2, Phase 2).

## Public interface
- `environment.py` — `GrimEnvironment`, overriding `execute(action: str) ->
  dict`. No shell runs in the control plane; `parse_grim` + `grim.dispatch`
  are the only calls it makes into the rest of the codebase.
- `grimoire.yaml` — mini config carrying the `system_template` protocol
  ladder (build plan §6). Data, not code — safe to hand-edit without
  touching `environment.py`.

## Invariants
- Any action that doesn't parse as one of the six verbs returns the canned
  protocol reminder — never falls through to actual execution.
- This module is the only caller of `verbs/*` on the agent's behalf; a
  human using the CLI directly goes through `cli.py`, not here.
- No new shell/subprocess call is ever added to this slice — that would
  reopen exactly the bypass D7 exists to close.
