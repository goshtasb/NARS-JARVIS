#!/bin/sh
# Generate the launchd LaunchAgent plist for the JARVIS daemon (ADR-049 Step 1, durability foundation).
#
# GENERATE-ONLY by design: it writes a validated plist with absolute paths and prints the install
# command — it never runs `launchctl load`, and it deliberately does NOT drop the plist into the active
# ~/Library/LaunchAgents (a RunAtLoad agent there auto-loads at the next login, which would silently
# re-daemonize a running JARVIS). The runtime lifecycle stays exactly as it is until you choose to cut
# over. Output defaults to a gitignored staging dir; pass an explicit path to target LaunchAgents at the
# real cutover.
#
#   sh src/ui/setup-launchd.sh                 # -> dist/com.nars-jarvis.daemon.plist (staging, lints, no load)
#   sh src/ui/setup-launchd.sh "$HOME/Library/LaunchAgents/com.nars-jarvis.daemon.plist"   # cutover target
set -e
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"

# ── absolute resolution (the whole point — launchd has no $PATH, no cwd, no ~/.zshrc) ──
PY="$root/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
SRC="$root/src"
NAR="$root/OpenNARS-for-Applications/NAR"
LLM="$root/models/qwen2.5-7b-instruct-q4_k_m.gguf"
[ -f "$LLM" ] || LLM="$root/models/qwen2.5-3b-instruct-q4_k_m.gguf"
EMBED="$root/models/nomic-embed-text-v1.5.f16.gguf"
DB="$root/src/jarvis.db"                       # absolute, CURRENT location — no orphaning at cutover
LOG="$HOME/Library/Logs/nars-jarvisd.log"

OUT="${1:-$root/dist/com.nars-jarvis.daemon.plist}"
mkdir -p "$(dirname "$OUT")"

# KeepAlive + ThrottleInterval=30 spin-damp a crash loop (the WAL's poison-pill attempt-counter is the
# real stop, and lands with the routine model). WorkingDirectory pins src/ so jarvis.db is never
# cwd-relative again.
cat > "$OUT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.nars-jarvis.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>-m</string>
        <string>service</string>
    </array>
    <key>WorkingDirectory</key><string>$SRC</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NARS_JARVIS_LLM_GGUF</key><string>$LLM</string>
        <key>NARS_JARVIS_EMBED_GGUF</key><string>$EMBED</string>
        <key>NARS_JARVIS_NAR_BIN</key><string>$NAR</string>
        <key>NARS_JARVIS_DB</key><string>$DB</string>
        <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

# ── validate the XML is structurally flawless before anyone trusts it ──
if plutil -lint "$OUT" >/dev/null; then
    echo "[setup-launchd] generated + linted: $OUT"
    echo "[setup-launchd] python:  $PY"
    echo "[setup-launchd] workdir: $SRC"
    echo "[setup-launchd] NAR:     $NAR"
    echo "[setup-launchd] NOT loaded. To cut over to a persistent login agent when ready:"
    echo "    cp \"$OUT\" \"\$HOME/Library/LaunchAgents/com.nars-jarvis.daemon.plist\""
    echo "    launchctl bootstrap gui/\$(id -u) \"\$HOME/Library/LaunchAgents/com.nars-jarvis.daemon.plist\""
else
    echo "[setup-launchd] plutil -lint FAILED for $OUT" >&2
    exit 1
fi
