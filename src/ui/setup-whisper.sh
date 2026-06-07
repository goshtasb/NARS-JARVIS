#!/bin/sh
# One-time setup for offline speech-to-text: install whisper.cpp (Homebrew) + download a small
# English model. Network is used ONCE here (a dependency install, like the GGUF weights download);
# the runtime itself stays fully offline. After this, push-to-talk works. Usage: ui/setup-whisper.sh
set -e
root="$(cd "$(dirname "$0")/../.." && pwd)"
model="$root/models/ggml-base.en.bin"

if ! command -v whisper-cli >/dev/null 2>&1; then
  echo "installing whisper.cpp via Homebrew…"
  brew install whisper-cpp
fi

if [ ! -f "$model" ]; then
  echo "downloading ggml-base.en model (~142MB) -> $model"
  curl -L --fail -o "$model" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
fi

echo "whisper ready: $(command -v whisper-cli)"
echo "model:        $model"
echo "the daemon auto-detects both; restart it (or run.sh) to enable voice."
