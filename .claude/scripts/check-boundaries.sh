#!/usr/bin/env bash
# .claude/scripts/check-boundaries.sh — hand-authored (Python has no
# packaged /setup generator yet — see .claude/setup-state.json). CI's
# non-interactive counterpart to the fence hook's slice-boundary rule.
#
# Grep-based heuristic, not a real import graph — good enough to catch the
# common case: a slice reaching across to another slice instead of through
# the shared kernel (grim.db / grim.cli). Mirrors the TS reference's own
# check-boundaries.sh (references/stack-ts.md), adapted for Python import
# syntax (`from grim.X import`, `from ..X import`, `import grim.X`).
set -eo pipefail

SLICE_ROOT="src/grim"
SLICES=(verbs exec adapter seeds)
SHARED_MODULES=(db cli)

[ -d "$SLICE_ROOT" ] || { echo "no $SLICE_ROOT/ directory — nothing to check."; exit 0; }

is_shared() {
  local mod="$1"
  for s in "${SHARED_MODULES[@]}"; do [ "$mod" = "$s" ] && return 0; done
  return 1
}

VIOLATIONS=0
for slice in "${SLICES[@]}"; do
  slice_dir="$SLICE_ROOT/$slice"
  [ -d "$slice_dir" ] || continue
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    file="${hit%%:*}"
    mod=$(echo "$hit" | grep -oE '(from grim\.[a-zA-Z0-9_]+|from \.\.[a-zA-Z0-9_]+|import grim\.[a-zA-Z0-9_]+)' | grep -oE '[a-zA-Z0-9_]+$' || true)
    [ -z "$mod" ] && continue
    [ "$mod" = "$slice" ] && continue
    is_shared "$mod" && continue
    echo "boundary violation: $file imports grim.$mod (slice: $slice)" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
  done < <(grep -rnE '^(from grim\.[a-zA-Z0-9_]+ import|from \.\.[a-zA-Z0-9_]+ import|import grim\.[a-zA-Z0-9_]+)' "$slice_dir" --include="*.py" 2>/dev/null || true)
done

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "$VIOLATIONS boundary violation(s) found." >&2
  exit 1
fi
echo "boundaries clean."
