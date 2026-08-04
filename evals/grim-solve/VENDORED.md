# Vendored eval fixtures — provenance

Fetched 2026-08-04. Third-party benchmark data, excluded from ruff/mypy
(`pyproject.toml [tool.ruff] extend-exclude`).

## ARC-AGI-1 public training set — Apache-2.0 (see licenses/ARC-AGI-LICENSE)

Source: https://github.com/fchollet/ARC-AGI (master @ fetch date),
`data/training/<id>.json`. Each task.json here is the upstream task with the
test pair's OUTPUT removed — `{"train": [...], "test_input": <grid>}` — so
the workspace the agent sees never contains the answer. The expected output
grid lives only in the task yaml's `expected:` key.

| task | upstream id | transformation | difficulty |
|---|---|---|---|
| arc-0d3d703e | 0d3d703e | per-color bijection on a 3x3 grid | easy |
| arc-1e0a9b12 | 1e0a9b12 | gravity: non-zero cells fall to the bottom of their column | medium |
| arc-3631a71a | 3631a71a | symmetry repair on a noisy 30x30 grid | hard |
