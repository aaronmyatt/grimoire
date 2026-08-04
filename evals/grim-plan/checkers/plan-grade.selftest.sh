#!/usr/bin/env bash
# Self-test for the plan-grade Checker — no API cost, no model involved.
# One golden-pass and one fail case per kind, plus the validator-specific
# behaviors (NL parsing, precondition rejection) and config-error exit 2.
# Values are vendored task data hardcoded for self-containment.
# Run: ./plan-grade.selftest.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$HERE/plan-grade"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fails=0
check() { # check <label> <want_exit> <output-text> [extra env as K=V ...]
  local label="$1" want="$2" text="$3"; shift 3
  local rd="$TMP/$RANDOM$RANDOM"
  mkdir -p "$rd"
  printf '%s\n' "$text" > "$rd/output.txt"
  env "$@" SMEVALS_RUN_DIR="$rd" "$CHECKER" >/dev/null 2>&1
  local got=$?
  if [ "$want" -eq "$got" ]; then echo "PASS $label"; else echo "FAIL $label (want exit $want, got $got)"; fails=$((fails+1)); fi
}

TRIP_ENV=(SMEVALS_TASK_KIND=trip
  "SMEVALS_TASK_CITIES=Helsinki**Barcelona**Florence" "SMEVALS_TASK_DURATIONS=5**5**6")
TRIP_GOLDEN='Here is the trip plan for visiting the 3 European cities for 14 days:

**Day 1-5:** Arriving in Helsinki and visit Helsinki for 5 days.
**Day 5:** Fly from Helsinki to Barcelona.
**Day 5-9:** Visit Barcelona for 5 days.
**Day 9:** Fly from Barcelona to Florence.
**Day 9-14:** Visit Florence for 6 days.'
check "trip-golden-passes" 0 "$TRIP_GOLDEN" "${TRIP_ENV[@]}"
check "trip-wrong-city-fails" 1 "${TRIP_GOLDEN//Helsinki/Atlantis}" "${TRIP_ENV[@]}"
check "trip-garbage-fails" 1 "I could not find a plan." "${TRIP_ENV[@]}"

CAL_ENV=(SMEVALS_TASK_KIND=calendar
  "SMEVALS_TASK_GOLDEN=Here is the proposed time: Monday, 14:30 - 15:00")
check "calendar-golden-passes" 0 "Here is the proposed time: Monday, 14:30 - 15:00" "${CAL_ENV[@]}"
check "calendar-wrong-time-fails" 1 "Here is the proposed time: Monday, 13:30 - 14:00" "${CAL_ENV[@]}"

BW_ENV=(SMEVALS_TASK_KIND=blocksworld
  'SMEVALS_TASK_INIT=[["on","blue","red"],["on","orange","blue"],["ontable","red"],["ontable","yellow"],["clear","orange"],["clear","yellow"],["handempty"]]'
  'SMEVALS_TASK_GOAL=[["on","blue","red"],["on","yellow","orange"]]')
check "bw-sexpr-golden-passes" 0 '(pick-up yellow)
(stack yellow orange)' "${BW_ENV[@]}"
check "bw-nl-format-passes" 0 'pick up the yellow block
stack the yellow block on top of the orange block' "${BW_ENV[@]}"
# pick-up requires ontable+clear+handempty; orange sits on blue, so this must
# be rejected by the PRECONDITION check, not by goal comparison.
check "bw-precondition-violation-fails" 1 '(pick-up orange)
(stack orange yellow)' "${BW_ENV[@]}"
check "bw-garbage-fails" 1 "no plan found" "${BW_ENV[@]}"

MY_ENV=(SMEVALS_TASK_KIND=mystery
  'SMEVALS_TASK_INIT=[["craves","a","b"],["harmony"],["planet","b"],["province","a"]]'
  'SMEVALS_TASK_GOAL=[["pain","a"]]')
check "mystery-feast-passes" 0 '(feast a b)' "${MY_ENV[@]}"
check "mystery-unmet-goal-fails" 1 '(succumb a)' "${MY_ENV[@]}"

# Missing/invalid config is exit 2, never a fail-grade.
env -u SMEVALS_RUN_DIR SMEVALS_TASK_KIND=trip "$CHECKER" >/dev/null 2>&1
got=$?
if [ "$got" -eq 2 ]; then echo "PASS missing-env-is-config-error"; \
  else echo "FAIL missing-env-is-config-error (want exit 2, got $got)"; fails=$((fails+1)); fi
check "unknown-kind-is-config-error" 2 "anything" SMEVALS_TASK_KIND=sudoku

[ "$fails" -eq 0 ] && echo "plan-grade selftest: all green" || echo "plan-grade selftest: $fails failure(s)"
exit "$fails"
