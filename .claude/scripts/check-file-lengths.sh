#!/usr/bin/env bash
# .claude/scripts/check-file-lengths.sh — hand-authored. Ruff has no native
# max-lines-per-file rule (unlike ESLint's `max-lines`), so this is the
# mechanical enforcement for budgets.json's .budgets.fileLines ceiling.
set -eo pipefail

FILE_LINES_BUDGET=500

DIRS=()
for d in src evals surfaces; do [ -d "$d" ] && DIRS+=("$d"); done
if [ ${#DIRS[@]} -eq 0 ]; then
  echo "no source directories found — nothing to check."
  exit 0
fi

VIOLATIONS=0
while IFS= read -r -d '' f; do
  n=$(wc -l < "$f" | tr -d ' ')
  if [ "$n" -gt "$FILE_LINES_BUDGET" ]; then
    echo "file-length violation: $f ($n lines, budget $FILE_LINES_BUDGET)" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
  fi
done < <(find "${DIRS[@]}" -name '*.py' -print0 2>/dev/null)

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "$VIOLATIONS file-length violation(s) found." >&2
  exit 1
fi
echo "file lengths clean."
