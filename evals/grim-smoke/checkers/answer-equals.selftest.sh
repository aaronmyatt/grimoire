#!/usr/bin/env bash
# Self-asserting, deterministic check of the answer-equals Checker — no model
# call, no API cost (CLAUDE.md §2.3). Run it directly any time:
#   ./evals/grim-smoke/checkers/answer-equals.selftest.sh
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
checker="$here/answer-equals"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

# $1 description  $2 expected exit code  $3 output.txt contents  $4 expected env
expect_exit() {
  printf '%s' "$3" >"$tmp/output.txt"
  SMEVALS_RUN_DIR="$tmp" SMEVALS_TASK_EXPECTED="$4" "$checker" >/dev/null 2>&1
  local got=$?
  if [ "$got" -ne "$2" ]; then
    echo "FAIL: $1 — wanted exit $2, got $got"
    fails=$((fails + 1))
  else
    echo "ok:   $1"
  fi
}

expect_exit "exact match passes"           0 "55"     "55"
expect_exit "trims surrounding whitespace" 0 "  55 "  "55"
expect_exit "trailing newline still passes" 0 $'6765\n' "6765"
expect_exit "wrong answer fails"           1 "54"     "55"

# A missing `expected` is a config error (exit 2), distinct from an honest miss.
printf '%s' "55" >"$tmp/output.txt"
SMEVALS_RUN_DIR="$tmp" "$checker" >/dev/null 2>&1
if [ $? -eq 2 ]; then
  echo "ok:   missing expected → exit 2"
else
  echo "FAIL: missing expected should exit 2"
  fails=$((fails + 1))
fi

if [ "$fails" -eq 0 ]; then
  echo "all answer-equals self-tests passed"
  exit 0
fi
echo "$fails self-test(s) failed"
exit 1
