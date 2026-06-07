# ui — native macOS menu-bar app (Phase 2)

## Overview
The native skin over the headless daemon (`service/`). A menu-bar app (`NSStatusItem` + `NSPopover`)
that is a **strictly thin client**: zero reasoning, zero LLM orchestration, zero state beyond its own
transcript view. It connects to the daemon's unix-domain socket, speaks the line/JSON protocol
(`service/protocol.py`), renders responses, and surfaces sentinel alerts / intervention prompts as
native notifications. The brain lives entirely in the daemon (ADR-003); this is presentation only.

## Usage
```bash
ui/build.sh            # compile -> build/JARVIS.app (swiftc, no Xcode), ad-hoc signed
ui/run-ui.sh           # ensure the daemon is up (with models), build if needed, open the app
```
Headless verification (no GUI needed):
```bash
ui/build/JARVIS.app/Contents/MacOS/JARVIS --check   # connect to the daemon, round-trip, exit
swiftc -O ui/JarvisClient.swift ui/probe_main.swift -o /tmp/jarvis-probe && /tmp/jarvis-probe <sock>
```
In the popover, type `learn …`, `ask …`, `tell …` (a bare line is treated as a question).

## Key Components
- **`JarvisClient.swift`** — the Swift side of the IPC bridge: POSIX `AF_UNIX` socket, a background
  reader thread, line/JSON framing, request/response correlation by id, and an `onEvent` callback.
  Mirrors the Python `Client`. The only transport code.
- **`AppDelegate.swift`** — status item + popover, wires `JarvisClient`, and `UNUserNotificationCenter`
  (alerts → banners; intervention → a notification with **Hide apps / Not now** actions that reply to
  the daemon's pending intervention — the native replacement for the dropped osascript hack).
- **`ChatView.swift`** — the popover view: transcript + input. Sends the typed command, renders the reply.
- **`main.swift`** — `.accessory` entry (menu-bar only); `--check` runs a headless IPC self-test.
- **`probe_main.swift`** — standalone headless bridge verifier (`jarvis-probe`).
- **`build.sh` / `run-ui.sh`** — bundle build and convenience launcher.

## App Sandbox decision (deliberate)
The app is built as a **non-sandboxed** `.app` via `swiftc` (no Xcode project, no `App Sandbox`
entitlement). A sandboxed app cannot reach a unix socket in an arbitrary directory; rather than add
an App Group container, we accept no-sandbox because this is a **local-first developer tool** that
already runs unsandboxed Python with the same privileges. The daemon's socket lives in `$TMPDIR`
(`/tmp/nars-jarvis.sock`), directly reachable. If this ever ships to non-developers, revisit via an
App Group shared container. See ADR-004.

It is **AppKit, not SwiftUI's `App` lifecycle**, and built with `swiftc` rather than Xcode — a
deliberate choice to match the project's existing `swiftc` toolchain (the sensor agent) and keep the
build dependency-free. The result is still a native menu-bar app. See ADR-004.

## Verified vs. human-verified
- **Verified headlessly:** compiles; bundle builds + ad-hoc signs; the app binary connects to a live
  daemon and round-trips a request (`--check` → `CHECK-OK`); the standalone probe round-trips
  `tell`/`ask`/`status`.
- **Requires a human at the GUI:** the menu-bar item rendering, popover chat interaction, and
  notification banners (notification authorization shows a one-time system dialog on first launch).

## Dependencies
AppKit, Foundation, UserNotifications, Darwin (system frameworks). Talks only to `service/` over the
socket. No third-party packages.

## Related ADRs
[ADR-003](../../docs/adrs/ADR-003-headless-daemon-ipc.md) (the daemon/IPC it connects to),
[ADR-004](../../docs/adrs/ADR-004-macos-menubar-ui.md) (this app's framework/sandbox choices).
