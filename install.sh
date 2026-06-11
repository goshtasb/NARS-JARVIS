#!/bin/sh
# NARS-JARVIS one-command installer (ADR-043) — Apple Silicon Macs.
#
#   curl -fsSL https://raw.githubusercontent.com/goshtasb/NARS-JARVIS/main/install.sh | sh
#
# What it does (and asks before anything large):
#   1. Verifies the platform (macOS arm64) and Xcode Command Line Tools.
#   2. Clones the repo (or updates it, if you run this from inside a checkout).
#   3. Creates an isolated Python venv and installs the (offline-capable) dependencies.
#   4. Downloads the PREBUILT ONA reasoner binary from the GitHub release — no C toolchain needed
#      (upstream MIT; provenance + license ship inside the archive). Verified by SHA256.
#   5. Offers the local model downloads (chat 7B ~4.7 GB, embedder ~270 MB, voice ~140 MB) — your
#      machine, your bandwidth, your call. Skipping any of them degrades gracefully.
#   6. Walks you through the macOS permission grants it can NOT (and should not) automate, then
#      builds and launches the menu-bar app.
#
# Honest scope: Apple Silicon only. The assistant's brain is a local 7B running on Metal; on an
# Intel Mac it would think at unusable speeds, so we don't pretend to support it.
set -e

REPO="https://github.com/goshtasb/NARS-JARVIS"
RELEASE_TAG="v1.14.4"
ONA_ASSET="ona-macos-arm64.tar.gz"
ONA_SHA256="ef43f7b33795c51c12dc2017adfb8196d7bfd4fc6bdf4ae7ae19ba1b8b6f14d4"
LLM_URL="https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
EMBED_URL="https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.f16.gguf"
VOICE_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"

say()  { printf '\033[1;34m[jarvis-install]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[jarvis-install]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. platform gates ──
[ "$(uname -s)" = "Darwin" ] || fail "macOS only."
[ "$(uname -m)" = "arm64" ] || fail "Apple Silicon (arm64) only — the local 7B needs Metal; Intel \
Macs would run it at unusable speeds, so this installer honestly refuses rather than disappoint."
xcode-select -p >/dev/null 2>&1 || { say "Xcode Command Line Tools required — accept the dialog, then re-run."; exec xcode-select --install; }
command -v python3 >/dev/null || fail "python3 not found (it ships with the Command Line Tools)."

# ── 2. the repo ──
if [ -f "./src/jarvis.py" ]; then
  root="$(pwd)"
  say "Using existing checkout: $root"
else
  root="$HOME/NARS-JARVIS"
  if [ -d "$root/.git" ]; then say "Updating $root"; git -C "$root" pull --ff-only; else
    say "Cloning into $root"; git clone "$REPO" "$root"; fi
fi
cd "$root"

# ── 3. isolated python env ──
if [ ! -x ".venv/bin/python3" ]; then
  say "Creating Python venv (.venv) — system Python stays untouched"
  python3 -m venv .venv
fi
say "Installing Python dependencies (llama-cpp-python compiles its Metal kernels — a few minutes)"
./.venv/bin/python3 -m pip install -q --upgrade pip
./.venv/bin/python3 -m pip install -q -r requirements.txt

# ── 4. prebuilt ONA reasoner (no C toolchain needed) ──
if [ ! -x "OpenNARS-for-Applications/NAR" ]; then
  say "Fetching the prebuilt ONA reasoner binary ($RELEASE_TAG release asset)"
  mkdir -p OpenNARS-for-Applications
  curl -fL --progress-bar -o "/tmp/$ONA_ASSET" "$REPO/releases/download/$RELEASE_TAG/$ONA_ASSET"
  echo "$ONA_SHA256  /tmp/$ONA_ASSET" | shasum -a 256 -c - >/dev/null || fail "ONA archive checksum mismatch — refusing to install an unverified binary."
  tar -xzf "/tmp/$ONA_ASSET" -C OpenNARS-for-Applications
  say "ONA binary verified (SHA256) and installed — provenance in OpenNARS-for-Applications/PROVENANCE.txt"
else
  say "ONA reasoner already present — keeping it"
fi

# ── 5. local models (optional, large, asked one by one) ──
mkdir -p models
fetch_model() { # $1 prompt  $2 url  $3 dest
  [ -f "$3" ] && { say "$(basename "$3") already present — keeping it"; return 0; }
  printf '\033[1;34m[jarvis-install]\033[0m %s [y/N] ' "$1"
  read -r yn </dev/tty || yn=n
  case "$yn" in [Yy]*) curl -fL --progress-bar -o "$3" "$2" && say "saved $(basename "$3")";; *) say "skipped";; esac
}
say "Models are downloaded from Hugging Face directly to your machine (nothing is uploaded anywhere)."
fetch_model "Download the chat brain — Qwen2.5-7B-Instruct Q4_K_M (~4.7 GB)? Without it, NL answers are limited." "$LLM_URL" "models/qwen2.5-7b-instruct-q4_k_m.gguf"
fetch_model "Download the grounding embedder — nomic-embed-text (~270 MB)? Without it, semantic grounding is off." "$EMBED_URL" "models/nomic-embed-text-v1.5.f16.gguf"
fetch_model "Download the voice model — whisper base.en (~140 MB)? Without it, push-to-talk is off." "$VOICE_URL" "models/ggml-base.en.bin"
say "Optional: JS-rendered web pages (weather sites etc.) need Chromium for the research loop:"
printf '\033[1;34m[jarvis-install]\033[0m Install headless Chromium for rendered web research (~160 MB)? [y/N] '
read -r yn </dev/tty || yn=n
case "$yn" in [Yy]*) ./.venv/bin/python3 -m playwright install chromium;; *) say "skipped (web research degrades to static pages)";; esac

# ── 6. the permissions this script will NOT automate (that would be a vulnerability) ──
say "Building the menu-bar app…"
sh src/ui/setup-signing.sh
sh src/ui/build.sh
cat <<'TCC'

  ┌─────────────────────────────────────────────────────────────────────┐
  │  macOS permissions — these are YOURS to grant; no script can (or    │
  │  should) do it for you:                                             │
  │   • Accessibility  → required for GUI actions (clicking controls)  │
  │   • Microphone     → required only for push-to-talk voice          │
  │  System Settings opens now; add/enable JARVIS in Accessibility.    │
  └─────────────────────────────────────────────────────────────────────┘
TCC
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true

say "Launching JARVIS (🔵 appears in your menu bar)…"
sh src/ui/run-ui.sh
say "Done. Chat from the menu bar, or terminal: ./run.sh"
