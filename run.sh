#!/bin/sh
# Launch the NARS-JARVIS console with the local models wired (LLM + grounding embedder).
# Usage:  ./run.sh
here="$(cd "$(dirname "$0")" && pwd)"
# Prefer the installer-created venv (ADR-043) so system Python stays untouched; no venv -> system python3.
[ -x "$here/.venv/bin/python3" ] && PATH="$here/.venv/bin:$PATH"
# Prefer the 7B brain (LLM-first, ADR-007); fall back to the 3B if the 7B isn't downloaded yet.
LLM="$here/models/qwen2.5-7b-instruct-q4_k_m.gguf"
[ -f "$LLM" ] || LLM="$here/models/qwen2.5-3b-instruct-q4_k_m.gguf"
EMBED="$here/models/nomic-embed-text-v1.5.f16.gguf"
[ -f "$LLM" ]   && export NARS_JARVIS_LLM_GGUF="$LLM"   || echo "[warn] chat model missing: $LLM (NL answers will be limited)"
[ -f "$EMBED" ] && export NARS_JARVIS_EMBED_GGUF="$EMBED" || echo "[warn] embed model missing: $EMBED (grounding off)"
cd "$here/src" && exec python3 console.py
