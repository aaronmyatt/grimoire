#!/usr/bin/env bash
# Thin launcher for `mini -c grimoire.yaml`: mini's own -o/--output default
# is a single fixed path (overwritten every run — see mini.py's
# DEFAULT_OUTPUT_FILE), and grimoire.yaml's static config can't compute a
# fresh one itself (only system_template/instance_template go through
# Jinja; output_path is consumed as a literal Path). So this script picks
# a unique /tmp path per invocation and passes it as -o, giving every run
# its own saved trajectory JSON to inspect after a crash or silent stop.
#
# ALWAYS launch through this script (or pass `-c grimoire.yaml` yourself).
# grimoire.yaml carries not just the prompt templates but the
# environment_class (GrimEnvironment, the six-verb enforcement point) and
# agent_class (GrimAgent). Invoking `uv run mini` with only a partial
# override like `-c model.model_class=...` and no grimoire.yaml silently
# falls back to stock mini + raw bash — not the grim sandbox — or, if the
# templates are also absent, crashes on a pydantic "field required" error.
set -euo pipefail

adapter_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
traj_path="/tmp/grimoire-mini-$(date +%Y%m%dT%H%M%S)-$$.traj.json"

# `--stream` opts into GrimStreamingTextbasedModel (live token-by-token
# terminal output) without hand-assembling a second -c spec — the fragile
# step that's easy to drop from a multi-line command. mini merges -c specs
# left-to-right (recursive_merge in run/mini.py), so appending this
# override AFTER grimoire.yaml wins over the yaml's model.model_class.
# Every other argument is forwarded to mini untouched.
stream_class="grim.adapter.streaming_model.GrimStreamingTextbasedModel"
stream_override=()
forwarded=()
for arg in "$@"; do
  if [ "$arg" = "--stream" ]; then
    stream_override=(-c "model.model_class=${stream_class}")
  else
    forwarded+=("$arg")
  fi
done

echo "[grim] trajectory: $traj_path" >&2
# `${arr[@]+"${arr[@]}"}` expands to nothing when the array is empty
# instead of tripping `set -u` on older bash (macOS ships 3.2).
exec uv run mini \
  -c "$adapter_dir/grimoire.yaml" \
  ${stream_override[@]+"${stream_override[@]}"} \
  -o "$traj_path" \
  ${forwarded[@]+"${forwarded[@]}"}
