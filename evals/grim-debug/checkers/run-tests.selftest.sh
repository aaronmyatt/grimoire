#!/usr/bin/env bash
# Self-test for the run-tests Checker — no API cost, no model involved.
# Exercises the four behaviors the Grade depends on: config errors exit 2,
# a still-buggy workspace fails, a genuinely fixed workspace passes, a
# tampered test driver is restored (anti-cheat), and an infinite loop is
# cut off by the timeout. Run: ./run-tests.selftest.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$HERE/run-tests"
FILES="$HERE/../tasks/files"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fails=0
expect() { # expect <label> <want_exit> <got_exit>
  if [ "$2" -eq "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (want exit $2, got $3)"; fails=$((fails+1)); fi
}

fresh_ws() { # fresh_ws <task-dir-name> — new run dir with a workspace copy
  local run_dir="$TMP/$RANDOM$RANDOM"
  mkdir -p "$run_dir/workspace"
  cp -R "$FILES/$1/." "$run_dir/workspace/"
  echo "$run_dir"
}

# 1. Missing env is a config error (exit 2), never a fail-grade.
env -u SMEVALS_RUN_DIR SMEVALS_TASK_FILES=quixbugs-quicksort \
  SMEVALS_TASK_PRISTINE="run_tests.py" "$CHECKER" >/dev/null 2>&1
expect "missing-env-is-config-error" 2 $?

# 2. The vendored buggy program fails the tests.
RD="$(fresh_ws quixbugs-quicksort)"
SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_FILES=quixbugs-quicksort \
  SMEVALS_TASK_PRISTINE="run_tests.py quicksort.testcases.json" "$CHECKER" >/dev/null
expect "buggy-workspace-fails" 1 $?

# 3. The known one-character fix passes (QuixBugs quicksort drops duplicates
#    because the `greater` filter uses > instead of >=).
RD="$(fresh_ws quixbugs-quicksort)"
sed -i '' 's/if x > pivot/if x >= pivot/' "$RD/workspace/quicksort.py"
SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_FILES=quixbugs-quicksort \
  SMEVALS_TASK_PRISTINE="run_tests.py quicksort.testcases.json" "$CHECKER" >/dev/null
expect "fixed-workspace-passes" 0 $?

# 4. Anti-cheat: a tampered driver that always "passes" is restored to
#    pristine, so the still-buggy code fails anyway.
RD="$(fresh_ws quixbugs-quicksort)"
printf 'import sys\nsys.exit(0)\n' > "$RD/workspace/run_tests.py"
SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_FILES=quixbugs-quicksort \
  SMEVALS_TASK_PRISTINE="run_tests.py quicksort.testcases.json" "$CHECKER" >/dev/null
expect "tampered-driver-restored" 1 $?

# 5. The sqrt bug loops forever; the timeout must convert that into a fail.
RD="$(fresh_ws quixbugs-sqrt)"
out="$(SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_FILES=quixbugs-sqrt \
  SMEVALS_TASK_PRISTINE="run_tests.py sqrt.testcases.json" \
  SMEVALS_CHECK_TIMEOUT_S=3 "$CHECKER")"
ec=$?
expect "infinite-loop-times-out" 1 $ec
case "$out" in *timed_out*true*) echo "PASS timeout-reported-in-metrics";; \
  *) echo "FAIL timeout-reported-in-metrics ($out)"; fails=$((fails+1));; esac

[ "$fails" -eq 0 ] && echo "run-tests selftest: all green" || echo "run-tests selftest: $fails failure(s)"
exit "$fails"
