#!/usr/bin/env bash
# Self-test for the swebench-eval Checker — no docker, no model, no network.
# The real harness is replaced through the SWEBENCH_EVAL_CMD seam with stubs
# that write (or withhold) a canned report, so every offline path is proven:
# config errors exit 2, an empty patch fails cheaply, a "resolved" report
# passes, an "unresolved" report fails, a vanished report is a config error.
# Run: ./swebench-eval.selftest.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$HERE/swebench-eval"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

IID="django__django-11039"
fails=0
expect() { # expect <label> <want_exit> <got_exit>
  if [ "$2" -eq "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (want exit $2, got $3)"; fails=$((fails+1)); fi
}

# Stub harnesses. The checker derives the report name as
# <MODEL_NAME>.grim-<instance_id>.json in its cwd; the stubs write exactly that.
STUB_OK="$TMP/stub-ok"; STUB_MISS="$TMP/stub-miss"; STUB_SILENT="$TMP/stub-silent"
cat > "$STUB_OK" <<'EOF'
#!/usr/bin/env bash
printf '{"resolved_ids": ["django__django-11039"], "unresolved_ids": []}' \
  > grimoire.grim-django__django-11039.json
EOF
cat > "$STUB_MISS" <<'EOF'
#!/usr/bin/env bash
printf '{"resolved_ids": [], "unresolved_ids": ["django__django-11039"]}' \
  > grimoire.grim-django__django-11039.json
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_SILENT"
chmod +x "$STUB_OK" "$STUB_MISS" "$STUB_SILENT"

fresh_case() { # fresh_case <name> <patch-content> — sets RD (run dir) and WS (grade cwd)
  RD="$TMP/$1/run"; WS="$TMP/$1/grade"
  mkdir -p "$RD" "$WS"
  printf '%s' "$2" > "$RD/output.txt"
}
PATCH=$'diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n'

# 1. Missing env is a config error (exit 2), never a fail-grade.
env -u SMEVALS_RUN_DIR -u SMEVALS_TASK_INSTANCE_ID "$CHECKER" >/dev/null 2>&1
expect "missing-env-is-config-error" 2 $?

# 2. An empty patch fails the grade cheaply — no harness launch at all.
fresh_case empty ""
out="$(cd "$WS" && SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_INSTANCE_ID="$IID" \
  SWEBENCH_EVAL_CMD="$STUB_SILENT" "$CHECKER")"
expect "empty-patch-fails" 1 $?
case "$out" in *'"empty_patch": true'*) echo "PASS empty-patch-in-metrics";; \
  *) echo "FAIL empty-patch-in-metrics ($out)"; fails=$((fails+1));; esac

# 3. A report listing the instance as resolved passes, and the predictions
#    artifact the harness was fed stays behind in the grade workspace.
fresh_case ok "$PATCH"
out="$(cd "$WS" && SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_INSTANCE_ID="$IID" \
  SWEBENCH_EVAL_CMD="$STUB_OK" "$CHECKER")"
expect "resolved-report-passes" 0 $?
case "$out" in *'"resolved": true'*) echo "PASS resolved-in-metrics";; \
  *) echo "FAIL resolved-in-metrics ($out)"; fails=$((fails+1));; esac
grep -q "$IID" "$WS/predictions.jsonl" \
  && echo "PASS predictions-artifact-kept" \
  || { echo "FAIL predictions-artifact-kept"; fails=$((fails+1)); }

# 4. A report without the instance in resolved_ids fails the grade.
fresh_case miss "$PATCH"
(cd "$WS" && SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_INSTANCE_ID="$IID" \
  SWEBENCH_EVAL_CMD="$STUB_MISS" "$CHECKER" >/dev/null)
expect "unresolved-report-fails" 1 $?

# 5. A harness that produces no report is a config error, not a grade.
fresh_case silent "$PATCH"
(cd "$WS" && SMEVALS_RUN_DIR="$RD" SMEVALS_TASK_INSTANCE_ID="$IID" \
  SWEBENCH_EVAL_CMD="$STUB_SILENT" "$CHECKER" >/dev/null 2>&1)
expect "no-report-is-config-error" 2 $?

[ "$fails" -eq 0 ] && echo "swebench-eval selftest: all green" || echo "swebench-eval selftest: $fails failure(s)"
exit "$fails"
