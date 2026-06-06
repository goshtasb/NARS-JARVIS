// NARS-JARVIS macOS telemetry sensor — unprivileged, push-based, TCC-free.
//
// Reads ONLY the frontmost app's BUNDLE ID (never window titles/contents -> no Accessibility /
// Screen-Recording prompt) and a coarse idle duration. Emits one discretized line per event to
// stdout; the Python side funnels them. Runs as an .accessory agent (no Dock icon), exits when
// its parent dies. No entitlements, no root, no polling of ps/top.
//
// Build:  swiftc -O sensor.swift -o .sensor.bin
import AppKit
import CoreGraphics
import Foundation

setbuf(stdout, nil)  // unbuffered: the parent's select() sees each line immediately

func emit(_ s: String) { print(s) }

func idleSeconds() -> Double {
    guard let anyInput = CGEventType(rawValue: ~0) else { return -1 }  // kCGAnyInputEventType
    return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyInput)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // background agent, no Dock icon, no main window

let ws = NSWorkspace.shared
let nc = ws.notificationCenter

// Initial frontmost so the parent has a starting context.
if let b = ws.frontmostApplication?.bundleIdentifier { emit("activate \(b)") }

// Push: the OS notifies us only when the frontmost app CHANGES (human-paced -> ~0 CPU).
nc.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: nil) { note in
    if let a = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
       let b = a.bundleIdentifier { emit("activate \(b)") }
}
// Push: a GUI app launched (novelty signal; headless processes need Endpoint Security — out of scope).
nc.addObserver(forName: NSWorkspace.didLaunchApplicationNotification, object: nil, queue: nil) { note in
    if let a = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
       let b = a.bundleIdentifier { emit("launch \(b)") }
}

// Coarse idle, low frequency (active/idle + breaks). Not an event tap -> no Accessibility prompt.
Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { _ in
    emit(String(format: "idle %.0f", idleSeconds()))
}
// Self-terminate if orphaned (parent exited -> reparented to launchd, pid 1).
Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { _ in
    if getppid() == 1 { exit(0) }
}

emit("ready")
app.run()
