#!/bin/sh
# Convenience launcher for the menu-bar app: ensure the daemon is up (with local models wired, like
# run.sh), build the app if needed, then open it. The app attaches to the same /tmp unix socket.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
# Prefer the installer-created venv (ADR-043) so the daemon uses the isolated deps; no venv -> system.
[ -x "$root/.venv/bin/python3" ] && PATH="$root/.venv/bin:$PATH"

LLM="$root/models/qwen2.5-7b-instruct-q4_k_m.gguf"          # prefer the 7B brain (ADR-007)
[ -f "$LLM" ] || LLM="$root/models/qwen2.5-3b-instruct-q4_k_m.gguf"
EMBED="$root/models/nomic-embed-text-v1.5.f16.gguf"
# Slice 4 hardening + 0.5B interim: the GBNF-constrained triage extraction runs on the SMALLEST model that
# can do constrained extraction. Prefer the 0.5B (~400 MB) so a background scan barely dents RAM; fall back
# to the 3B (~2 GB) if the 0.5B isn't downloaded yet (triage_model_path falls back further to the 7B). NOTE:
# GBNF guarantees the output SHAPE, not extraction ACCURACY — the 0.5B's accuracy is eval-gated (Issue #24)
# before it is trusted as the production default; until then the 3B remains the safe fallback.
TRIAGE="$root/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"
[ -f "$TRIAGE" ] || TRIAGE="$root/models/qwen2.5-3b-instruct-q4_k_m.gguf"
[ -f "$LLM" ]    && export NARS_JARVIS_LLM_GGUF="$LLM"
[ -f "$EMBED" ]  && export NARS_JARVIS_EMBED_GGUF="$EMBED"
[ -f "$TRIAGE" ] && export NARS_JARVIS_TRIAGE_GGUF="$TRIAGE"

SOCK="${NARS_JARVIS_SOCK:-${TMPDIR:-/tmp}/nars-jarvis.sock}"
export NARS_JARVIS_SOCK="$SOCK"

# Start the daemon only if one is actually LISTENING (ADR-017) — not merely a stale socket FILE.
if python3 -c "import socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(.5);sys.exit(s.connect_ex('$SOCK'))" 2>/dev/null; then
  echo "reusing the running JARVIS daemon."
else
  rm -f "$SOCK"                                   # clear any stale socket file left by a non-clean exit
  echo "starting JARVIS daemon (loads local models ~10-20s)…"
  ( cd "$root/src" && exec python3 -m service ) >"${TMPDIR:-/tmp}/nars-jarvisd.log" 2>&1 &
  for i in $(seq 1 600); do [ -S "$SOCK" ] && break; sleep 0.1; done
fi

# Build the app if missing OR if any Swift source is newer than the built binary (ship UI edits).
bin="$here/build/JARVIS.app/Contents/MacOS/JARVIS"
needbuild=0
[ -x "$bin" ] || needbuild=1
for s in "$here"/*.swift; do
  if [ "$s" -nt "$bin" ]; then needbuild=1; fi
done
[ "$needbuild" -eq 0 ] || "$here/build.sh"

# Always relaunch the FRESH build. A running instance is the OLD binary — just rebuilding on disk and
# leaving it running means rebuilds never appear. Kill it, then open the newly-built app.
if pgrep -f "build/JARVIS.app/Contents/MacOS/JARVIS" >/dev/null 2>&1; then
  echo "restarting JARVIS app with the latest build…"
  pkill -f "build/JARVIS.app/Contents/MacOS/JARVIS" 2>/dev/null || true
  for i in $(seq 1 20); do pgrep -f "build/JARVIS.app/Contents/MacOS/JARVIS" >/dev/null 2>&1 || break; sleep 0.1; done
fi
open "$here/build/JARVIS.app"
echo "JARVIS is in your menu bar (🔵). Daemon log: ${TMPDIR:-/tmp}/nars-jarvisd.log"
