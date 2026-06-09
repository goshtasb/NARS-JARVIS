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
  "$here/ChatView.swift" "$here/AppDelegate.swift" "$here/main.swift" \
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

# Ad-hoc sign so the bundle has an identity (UNUserNotificationCenter needs one). Best-effort.
codesign --force --deep --sign - "$app" 2>/dev/null \
  || echo "[warn] ad-hoc codesign failed; notifications may require manual approval"
echo "built $app"
