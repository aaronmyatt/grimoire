#!/usr/bin/env bash
# Self-test for the run-metrics observer Checker — no API cost, no model.
# Proves the observer contract on fabricated artifacts: exact counts from a
# traj + session-scoped DB; degraded inputs (no stamp, no env) still exit 0
# with metrics_ok=false; a no-library (baseline) stamp skips DB metrics
# without complaint. Run: ./run-metrics.selftest.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$HERE/run-metrics"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fails=0
expect() { # expect <label> <want_exit> <got_exit>
  if [ "$2" -eq "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (want exit $2, got $3)"; fails=$((fails+1)); fi
}
has() { # has <label> <fragment> <haystack>
  case "$3" in *"$2"*) echo "PASS $1";; *) echo "FAIL $1 (missing $2 in: $3)"; fails=$((fails+1));; esac
}

# Fixture: a mini-format trajectory (2 assistant steps, 1 FormatError
# observation), a grim-run.json stamp, and a schema-subset DB where session
# sess-this ran 3 scripts: the shell seed (escape), a prior-session script
# (reuse), and one it wrote itself.
RD="$TMP/run"; mkdir -p "$RD"
cat > "$RD/agent.traj.json" <<'EOF'
{"info": {"model_stats": {"instance_cost": 0.0123, "api_calls": 3},
  "config": {"environment": {"session_id": "sess-this"}},
  "exit_status": "Submitted", "submission": "ok"},
 "messages": [
  {"role": "system", "content": "s"},
  {"role": "user", "content": "t"},
  {"role": "assistant", "content": "", "tool_calls": []},
  {"role": "user", "content": "err", "extra": {"interrupt_type": "FormatError"}},
  {"role": "assistant", "content": "", "tool_calls": []}]}
EOF
python3 - "$RD/grimoire.db" <<'EOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.executescript("""
CREATE TABLE script (id INTEGER PRIMARY KEY, name TEXT, language TEXT, origin_session_id TEXT, seeded INT);
CREATE TABLE script_version (id INTEGER PRIMARY KEY, script_id INT);
CREATE TABLE execution (id INTEGER PRIMARY KEY, script_version_id INT, session_id TEXT);
INSERT INTO script VALUES (1,'shell','bash',NULL,1),(2,'prior_tool','python','sess-old',0),(3,'own_tool','python','sess-this',0);
INSERT INTO script_version VALUES (10,1),(20,2),(30,3);
INSERT INTO execution VALUES (1,10,'sess-this'),(2,20,'sess-this'),(3,30,'sess-this'),(4,20,'sess-old');
""")
conn.commit(); conn.close()
EOF
printf '{"session_id": "sess-this", "db": "%s", "db_mode": "cold"}\n' "$RD/grimoire.db" \
  > "$RD/grim-run.json"

# 1. Full fixture: exact traj + session-scoped db counts, metrics_ok true.
out="$(SMEVALS_RUN_DIR="$RD" "$CHECKER")"
expect "full-fixture-exit-0" 0 $?
has "metrics-ok"      '"metrics_ok": true' "$out"
has "steps"           '"steps": 2' "$out"
has "format-errors"   '"format_errors": 1' "$out"
has "api-calls"       '"api_calls": 3' "$out"
has "runs-total"      '"runs_total": 3' "$out"
has "runs-shell"      '"runs_shell": 1' "$out"
has "prior-reuse"     '"runs_prior_reuse": 1' "$out"
has "scripts-written" '"scripts_written": 1' "$out"
has "score-constant"  '"score": 1.0' "$out"
# Language lean: own_tool (python) written this session; runs = shell(bash)
# once + prior_tool/own_tool (python) twice.
has "lean-writes-python" '"writes_python": 1' "$out"
has "lean-runs-bash"     '"runs_bash": 1' "$out"
has "lean-runs-python"   '"runs_python": 2' "$out"

# 2. No grim-run.json: still exit 0, degraded and saying so.
RD2="$TMP/run2"; mkdir -p "$RD2"; cp "$RD/agent.traj.json" "$RD2/"
out="$(SMEVALS_RUN_DIR="$RD2" "$CHECKER")"
expect "no-stamp-exit-0" 0 $?
has "no-stamp-degraded" '"metrics_ok": false' "$out"

# 3. Baseline stamp (db: "") skips db metrics without degrading.
RD3="$TMP/run3"; mkdir -p "$RD3"; cp "$RD/agent.traj.json" "$RD3/"
printf '{"session_id": "", "db": "", "db_mode": "baseline"}\n' > "$RD3/grim-run.json"
out="$(SMEVALS_RUN_DIR="$RD3" "$CHECKER")"
expect "baseline-exit-0" 0 $?
has "baseline-ok" '"metrics_ok": true' "$out"
case "$out" in *'"runs_total"'*) echo "FAIL baseline-skips-db"; fails=$((fails+1));; \
  *) echo "PASS baseline-skips-db";; esac

# 4. Missing env: the observer still never fails the grade.
out="$(env -u SMEVALS_RUN_DIR "$CHECKER")"
expect "no-env-exit-0" 0 $?
has "no-env-degraded" '"metrics_ok": false' "$out"

[ "$fails" -eq 0 ] && echo "run-metrics selftest: all green" || echo "run-metrics selftest: $fails failure(s)"
exit "$fails"
