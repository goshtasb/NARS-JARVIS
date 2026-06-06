#!/bin/sh
# Launch the NARS-JARVIS console with the local models wired (LLM + grounding embedder).
# Usage:  ./run.sh
here="$(cd "$(dirname "$0")" && pwd)"
LLM="$here/models/qwen2.5-3b-instruct-q4_k_m.gguf"
EMBED="$here/models/nomic-embed-text-v1.5.f16.gguf"
[ -f "$LLM" ]   && export NARS_JARVIS_LLM_GGUF="$LLM"   || echo "[warn] chat model missing: $LLM (NL 'learn' will be limited)"
[ -f "$EMBED" ] && export NARS_JARVIS_EMBED_GGUF="$EMBED" || echo "[warn] embed model missing: $EMBED (grounding off)"
cd "$here/src" && exec python3 console.py
