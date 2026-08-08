#!/usr/bin/env bash
# Offline HyperFrames CLI wrapper.
#
# Resolves a local hyperframes install and runs it through node directly, so the
# render loop never hits the network mid-project (a mid-render `npx` fetch is how
# you get a version bump between draft and final).
#
# Override with:  HYPERFRAMES_BIN=/path/to/hyperframes.mjs tools/hf.sh check
set -euo pipefail

resolve() {
  if [[ -n "${HYPERFRAMES_BIN:-}" && -f "${HYPERFRAMES_BIN}" ]]; then
    echo "${HYPERFRAMES_BIN}"; return
  fi
  local candidates=(
    "./node_modules/hyperframes/bin/hyperframes.mjs"
    "${APPDATA:-$HOME}/npm/node_modules/hyperframes/bin/hyperframes.mjs"
    "$HOME/AppData/Roaming/npm/node_modules/hyperframes/bin/hyperframes.mjs"
  )
  for candidate in "${candidates[@]}"; do
    [[ -f "$candidate" ]] && { echo "$candidate"; return; }
  done
  # npx cache: hashed dirs, newest wins
  local cache="$HOME/AppData/Local/npm-cache/_npx"
  [[ -d "$cache" ]] || cache="$HOME/.npm/_npx"
  if [[ -d "$cache" ]]; then
    local hit
    hit=$(ls -td "$cache"/*/node_modules/hyperframes/bin/hyperframes.mjs 2>/dev/null | head -1 || true)
    [[ -n "$hit" ]] && { echo "$hit"; return; }
  fi
  return 1
}

BIN="$(resolve)" || {
  echo "hyperframes not found locally." >&2
  echo "Install once:  npm i -g hyperframes   (or set HYPERFRAMES_BIN)" >&2
  exit 127
}

exec node "$BIN" "$@"
