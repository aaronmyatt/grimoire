#!/usr/bin/env bash
# Thin launcher for `mini -c grimoire.yaml`: mini's own -o/--output default
# is a single fixed path (overwritten every run — see mini.py's
# DEFAULT_OUTPUT_FILE), and grimoire.yaml's static config can't compute a
# fresh one itself (only system_template/instance_template go through
# Jinja; output_path is consumed as a literal Path). So this script picks
# a unique /tmp path per invocation and passes it as -o, giving every run
# its own saved trajectory JSON to inspect after a crash or silent stop.
set -euo pipefail

adapter_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
traj_path="/tmp/grimoire-mini-$(date +%Y%m%dT%H%M%S)-$$.traj.json"

echo "[grim] trajectory: $traj_path" >&2
exec uv run mini -c "$adapter_dir/grimoire.yaml" -o "$traj_path" "$@"
