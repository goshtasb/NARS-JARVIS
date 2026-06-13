#!/bin/sh
# Build the native menu-bar app as a non-sandboxed .app bundle (deliberate: a local-first developer
# tool, so the daemon's /tmp unix socket is directly reachable — see README "App Sandbox decision").
# No Xcode project; swiftc compiles the sources, then we lay down a minimal bundle + ad-hoc sign so
# UNUserNotificationCenter has a bundle identity. Usage: ui/build.sh
set -e
here="$(cd "$(dirname "$0")" && pwd)"
app="$here/build/JARVIS.app"
macos="$app/Contents/MacOS"
rm -rf "$app"
mkdir -p "$macos"

swiftc -O \
  "$here/JarvisClient.swift" "$here/AudioRecorder.swift" "$here/HotKey.swift" \
  "$here/AXPermission.swift" "$here/AXSerializer.swift" "$here/AXActuator.swift" \
  "$here/ChatView.swift" "$here/ActionPicker.swift" "$here/HabitsView.swift" "$here/UnifiedCanvasView.swift" "$here/SummaryPDF.swift" \
  "$here/DesignSystem.swift" "$here/TabSwitcher.swift" "$here/WorkspaceController.swift" "$here/CloudMode.swift" "$here/AppDelegate.swift" "$here/main.swift" \
  -o "$macos/JARVIS"

cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>NARS-JARVIS</string>
  <key>CFBundleIdentifier</key><string>com.nars.jarvis</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>NARS-JARVIS uses the microphone for push-to-talk voice input (⌥Space).</string>
</dict></plist>
PLIST

# Sign with a STABLE self-signed identity (ADR-021) so the app's Designated Requirement is identity-
# based and constant across rebuilds — this keeps the macOS Accessibility (TCC) grant valid every
# rebuild instead of dying each time (the ad-hoc churn that broke GUI actuation repeatedly). Falls
# back to ad-hoc on a machine without the cert; run ui/setup-signing.sh once to create it.
IDENTITY="JARVIS Self-Signed"
if security find-identity -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
  codesign --force --deep --sign "$IDENTITY" "$app" 2>/dev/null \
    && echo "[sign] $IDENTITY — stable; Accessibility grant persists across rebuilds" \
    || echo "[warn] codesign with '$IDENTITY' failed"
else
  codesign --force --deep --sign - "$app" 2>/dev/null \
    || echo "[warn] ad-hoc codesign failed; notifications may require manual approval"
  echo "[sign] ad-hoc — run ui/setup-signing.sh once so the Accessibility grant survives rebuilds"
fi
echo "built $app"
