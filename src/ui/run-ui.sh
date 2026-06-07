#!/bin/sh
# Convenience launcher for the menu-bar app: ensure the daemon is up (with local models wired, like
# run.sh), build the app if needed, then open it. The app attaches to the same /tmp unix socket.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"

LLM="$root/models/qwen2.5-3b-instruct-q4_k_m.gguf"
EMBED="$root/models/nomic-embed-text-v1.5.f16.gguf"
[ -f "$LLM" ]   && export NARS_JARVIS_LLM_GGUF="$LLM"
[ -f "$EMBED" ] && export NARS_JARVIS_EMBED_GGUF="$EMBED"

SOCK="${NARS_JARVIS_SOCK:-${TMPDIR:-/tmp}/nars-jarvis.sock}"
export NARS_JARVIS_SOCK="$SOCK"

# Start the daemon only if its socket isn't already serving.
if [ ! -S "$SOCK" ]; then
  echo "starting JARVIS daemon (loads local models ~10-20s)…"
  ( cd "$root/src" && exec python3 -m service ) >"${TMPDIR:-/tmp}/nars-jarvisd.log" 2>&1 &
  for i in $(seq 1 600); do [ -S "$SOCK" ] && break; sleep 0.1; done
fi

[ -d "$here/build/JARVIS.app" ] || "$here/build.sh"
open "$here/build/JARVIS.app"
echo "JARVIS is in your menu bar (🔵). Daemon log: ${TMPDIR:-/tmp}/nars-jarvisd.log"
