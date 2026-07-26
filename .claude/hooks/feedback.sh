#!/usr/bin/env bash
# .claude/hooks/feedback.sh — hand-authored (Python stack has no packaged
# /setup generator yet — see .claude/setup-state.json). PostToolUse:
# streams ruff/mypy findings for the file just touched back to Claude via
# `additionalContext`. Never blocks — PostToolUse fires after the tool
# call already succeeded, so there's nothing left to block.
set -eo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
case "$FILE" in
  *.py) ;;
  *) exit 0 ;;
esac
command -v uv >/dev/null 2>&1 || exit 0

REPORT=""
if ! OUT=$(uv run ruff format --check "$FILE" 2>&1); then REPORT="$REPORT

ruff format:
$OUT"; fi
if ! OUT=$(uv run ruff check "$FILE" 2>&1); then REPORT="$REPORT

ruff check:
$OUT"; fi
if ! OUT=$(uv run mypy "$FILE" 2>&1); then REPORT="$REPORT

mypy:
$OUT"; fi

if [ -n "$REPORT" ]; then
  jq -n --arg ctx "$REPORT" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $ctx}}'
fi
exit 0
