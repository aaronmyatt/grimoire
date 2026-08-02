#!/usr/bin/env bash
# Root-level convenience launcher for quick manual testing of the grim
# agent: `./run.sh "your task here"`. Thin wrapper over the real adapter
# launcher (src/grim/adapter/run.sh) with defaults tuned for a throwaway
# trial run — a cheap model and a scratch script library — so you don't
# re-type env vars and flags every time.
#
#   $1              the initial task/prompt handed to the agent (required)
#   $2.. (optional) forwarded verbatim to the adapter launcher, e.g.
#                     ./run.sh "list my repos" --stream
#                     ./run.sh "list my repos" -m anthropic/claude-sonnet-4-5
#
# Override the defaults with env vars:
#   GRIM_MODEL  model passed to mini as -m   (default: deepseek/deepseek-v4-flash)
#   GRIM_DB     script-library SQLite path    (default: /tmp/grimoire-trial.db —
#               a disposable DB so trial runs never touch a real library; the
#               grim CLI reads this same var, see src/grim/db.py)
set -euo pipefail

if [ "$#" -eq 0 ] || [ -z "${1:-}" ]; then
  echo 'usage: ./run.sh "<initial prompt>" [extra mini/adapter args...]' >&2
  echo '  e.g. ./run.sh "list the public github repos for aaronmyatt"' >&2
  echo '       ./run.sh "summarize README.md" --stream' >&2
  exit 2
fi

task="$1"
shift # remaining args ($2..) forward to the adapter launcher untouched

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# `:=` assigns the default only when the var is unset/empty, so an
# exported GRIM_MODEL/GRIM_DB from your shell still wins.
# Ref: https://www.gnu.org/software/bash/manual/html_node/Shell-Parameter-Expansion.html
: "${GRIM_MODEL:=deepseek/deepseek-v4-flash}"
: "${GRIM_DB:=/tmp/grimoire-trial.db}"
export GRIM_DB

# cd into the repo so `uv run` (invoked inside the adapter launcher)
# resolves this project's venv regardless of where ./run.sh was called
# from. The adapter launcher adds `-c grimoire.yaml`, a fresh trajectory
# path, and its own `--stream` handling; grimoire.yaml already sets yolo
# mode, so no -y is needed here.
cd "$repo_dir"
exec ./src/grim/adapter/run.sh \
  -m "$GRIM_MODEL" \
  -t "$task" \
  "$@"
