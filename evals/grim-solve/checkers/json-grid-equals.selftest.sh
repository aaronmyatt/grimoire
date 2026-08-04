#!/usr/bin/env bash
# Self-test for the json-grid-equals Checker — no API cost, no model involved.
# Run: ./json-grid-equals.selftest.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$HERE/json-grid-equals"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fails=0
check() { # check <label> <want_exit> <output.txt contents>
  local rd="$TMP/$RANDOM$RANDOM"
  mkdir -p "$rd"
  printf '%s\n' "$3" > "$rd/output.txt"
  SMEVALS_RUN_DIR="$rd" SMEVALS_TASK_EXPECTED='[[1,2],[3,4]]' "$CHECKER" >/dev/null 2>&1
  local got=$?
  if [ "$2" -eq "$got" ]; then echo "PASS $1"; else echo "FAIL $1 (want exit $2, got $got)"; fails=$((fails+1)); fi
}

check "exact-grid-passes"        0 '[[1,2],[3,4]]'
check "fenced-and-prose-passes"  0 'The answer is:
```json
[[1, 2], [3, 4]]
```'
check "wrong-grid-fails"         1 '[[1,2],[3,5]]'
check "garbage-fails"            1 'I could not solve this puzzle.'
check "non-grid-json-fails"      1 '["a", "b"]'

# Missing env is a config error (exit 2), never a fail-grade.
env -u SMEVALS_RUN_DIR SMEVALS_TASK_EXPECTED='[[1]]' "$CHECKER" >/dev/null 2>&1
got=$?
if [ "$got" -eq 2 ]; then echo "PASS missing-env-is-config-error"; \
  else echo "FAIL missing-env-is-config-error (want exit 2, got $got)"; fails=$((fails+1)); fi

[ "$fails" -eq 0 ] && echo "json-grid-equals selftest: all green" || echo "json-grid-equals selftest: $fails failure(s)"
exit "$fails"
