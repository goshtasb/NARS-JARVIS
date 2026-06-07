# ADR-004: macOS menu-bar UI — AppKit via swiftc, non-sandboxed

## Status
Accepted

## Context
Phase 2 puts JARVIS in the menu bar as the native skin over the headless daemon (ADR-003). Two
toolchain/runtime decisions are difficult to reverse once UI code accretes, so they are recorded
here: (1) how the app is built and which UI framework it uses, and (2) how it reaches the daemon's
unix-domain socket given macOS sandboxing.

The constraint that forces (2): a sandboxed macOS app (Xcode's default) cannot open a unix socket at
an arbitrary filesystem path; it can only reach paths inside its container or a shared App Group.

## Decision
1. **Build with `swiftc` into a non-Xcode `.app` bundle, using AppKit** (`NSStatusItem` +
   `NSPopover`), not the SwiftUI `App` lifecycle. The project already compiles `sensor.swift` with
   `swiftc`; reusing that toolchain keeps the build dependency-free (no `.xcodeproj`, no Xcode
   required) and AppKit is the reliable path for a menu-bar agent under a plain `swiftc` compile.
2. **Ship the app non-sandboxed.** The daemon's socket lives in `$TMPDIR` and the app connects to it
   directly. We do not enable the App Sandbox / do not use an App Group container.

The bundle is ad-hoc code-signed so `UNUserNotificationCenter` has a bundle identity.

## Consequences
- **Easier:** no Xcode dependency; one `swiftc` toolchain across sensor + UI; the socket "just works"
  with no entitlement plumbing; the brain stays in the daemon (the app is ~3 small Swift files).
- **Harder / accepted:** no App Sandbox means the app runs with the user's normal privileges (already
  true of the Python daemon it fronts, so no new exposure for a local-first tool); not
  Mac-App-Store-distributable as-is; SwiftUI's declarative niceties are foregone for AppKit.
- **Notifications:** ad-hoc signing gives a bundle identity, but the first launch still shows the
  system notification-authorization dialog, and unsigned/ad-hoc apps can have limited notification
  reliability — acceptable for a local developer tool.

## Alternatives Considered
- **Xcode project + sandboxed SwiftUI app + App Group shared container for the socket:** rejected for
  now — adds an Xcode dependency and App Group/entitlement complexity for zero benefit to a
  single-user local tool. This is the path to revisit if JARVIS is ever distributed to non-developers.
- **TCP on 127.0.0.1 to dodge the sandbox socket-path limit:** rejected — a sandboxed app would then
  need the `network.client` entitlement, and it reintroduces a port (ADR-003 chose a filesystem
  socket precisely to avoid one).
- **Keep osascript banners / a Python-Tk window:** rejected — not native, not the ambient menu-bar
  companion the product calls for.
