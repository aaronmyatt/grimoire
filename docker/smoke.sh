#!/usr/bin/env bash
# Offline, no-API-key smoke test for the grim distributable image. Proves
# the image builds and its *non-LLM* surface works end to end:
#   - `grim init` seeds the starter library,
#   - a python seed executes (uv-based dispatch + a working shell in-image),
#   - the entrypoint fails fast when GRIM_MODEL is unset.
# The live LLM path needs a real provider key and is a separate manual check
# (see README "Run in a container") — deliberately not run here.
#
#   ./docker/smoke.sh                     # build tag `grimoire:smoke`, test it
#   IMAGE=grimoire:foo ./docker/smoke.sh  # test an already-built tag, no build
set -euo pipefail

image="${IMAGE:-grimoire:smoke}"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${IMAGE:-}" ]; then
	echo "== build $image ==" >&2
	docker build -t "$image" "$repo_dir"
fi

# Each check runs a fresh container (--rm). `set -e` turns any failed
# assertion (grep -q returning nonzero) into a failed smoke run.
run() { docker run --rm "$image" "$@"; }

echo "== init seeds the library ==" >&2
run grim list | grep -q shell
echo "  ok: seeds present" >&2

echo "== python dispatch works (uv run in-image) ==" >&2
# `shell` is a python seed that shells out; running it exercises uv-based
# python dispatch and a working /bin/sh inside the container.
run grim run shell -- echo hello | grep -q hello
echo "  ok: script executed" >&2

echo "== entrypoint fails fast without GRIM_MODEL ==" >&2
# A prompt with no model id must exit nonzero with a clear message, never
# hang or crash inside litellm. Invert the check: success == it failed.
if run "summarize something" 2>/dev/null; then
	echo "  FAIL: expected nonzero exit when GRIM_MODEL is unset" >&2
	exit 1
fi
echo "  ok: failed fast" >&2

echo "== all smoke checks passed ==" >&2
