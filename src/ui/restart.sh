#!/bin/sh
# Foolproof clean restart (ADR-017): quiesce EVERY JARVIS process + clear the socket, then relaunch
# via run-ui.sh. Env flags (NARS_JARVIS_TRACE / NARS_JARVIS_DRY_RUN / NARS_JARVIS_SOCK) pass through.
# Use this instead of ad-hoc kills — it makes the two-daemon / stale-socket footgun unhittable.
here="$(cd "$(dirname "$0")" && pwd)"
SOCK="${NARS_JARVIS_SOCK:-${TMPDIR:-/tmp}/nars-jarvis.sock}"

echo "quiescing JARVIS (daemon, sensor, app)…"
pkill -f "Python -m service"   2>/dev/null || true   # the daemon (cmd is 'Python -m service')
pkill -f "[.]sensor.bin"        2>/dev/null || true   # the Swift telemetry helper
pkill -f "build/JARVIS.app/Contents/MacOS/JARVIS" 2>/dev/null || true  # the menu-bar app
sleep 1
rm -f "$SOCK"                                          # clear the unix socket

exec "$here/run-ui.sh"                                 # clean start (inherits the env flags)
