#!/usr/bin/env bash
# Container entrypoint for the grim distributable. Two modes:
#   - Direct CLI/tool: first arg is a known binary -> run it verbatim (the
#     human surface; no model needed).
#   - LLM prompt (default): hand the args to `grim-agent`, the installable
#     yolo harness, which owns DB init, config resolution, the trajectory
#     path, and the unattended (`-y --exit-immediately`) launch.
#
# Run an LLM prompt:
#   docker run --rm -e GRIM_MODEL=anthropic/claude-sonnet-4-5 \
#       -e ANTHROPIC_API_KEY=sk-... IMAGE "summarize README.md"
#
# Poke the library directly (no model):
#   docker run --rm IMAGE grim list
#   docker run --rm IMAGE grim run shell -- echo hi
set -euo pipefail

# Passthrough: when the first arg is a known binary, run it verbatim so the
# image doubles as a plain grim/mini CLI (and bash/sh for debugging). These
# need a seeded DB, so init first (idempotent — cli.py cmd_init).
case "${1:-}" in
grim | mini | bash | sh)
	grim init >&2
	exec "$@"
	;;
esac

if [ "$#" -eq 0 ]; then
	echo 'usage: docker run ... IMAGE "<prompt>"   (or: IMAGE grim <verb> ...)' >&2
	exit 2
fi

# Fail fast on a missing model id rather than letting litellm raise an opaque
# error mid-run. The provider API key itself is litellm's concern — it reads
# <PROVIDER>_API_KEY from the environment directly; we only guarantee the
# model id is present. Ref: bash ${VAR:?message}
# https://www.gnu.org/software/bash/manual/html_node/Shell-Parameter-Expansion.html
: "${GRIM_MODEL:?set GRIM_MODEL (e.g. anthropic/claude-sonnet-4-5) and the matching <PROVIDER>_API_KEY}"

# grim-agent takes a bare positional prompt as the task and forwards the rest
# to mini; it runs `grim init` itself and picks a fresh trajectory path (under
# $GRIM_TRAJ_DIR, default /tmp). One launch path, shared with native installs.
exec grim-agent "$@" -m "$GRIM_MODEL"
