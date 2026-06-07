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

// The app's SELF-DECLARED category from its Info.plist (Apple's fixed UTI taxonomy). A plain
// world-readable file -> no TCC. "-" when the app omits the key (rogue dev tools); the Python
// side then falls back to its override table / "other" bucket.
func categoryOf(_ app: NSRunningApplication) -> String {
    if let url = app.bundleURL, let bundle = Bundle(url: url),
       let cat = bundle.object(forInfoDictionaryKey: "LSApplicationCategoryType") as? String,
       !cat.isEmpty {
        return cat
    }
    return "-"
}

func idleSeconds() -> Double {
    guard let anyInput = CGEventType(rawValue: ~0) else { return -1 }  // kCGAnyInputEventType
    return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyInput)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // background agent, no Dock icon, no main window

let ws = NSWorkspace.shared
let nc = ws.notificationCenter

// Initial frontmost so the parent has a starting context.
if let fa = ws.frontmostApplication, let b = fa.bundleIdentifier { emit("activate \(b) \(categoryOf(fa))") }

// Push: the OS notifies us only when the frontmost app CHANGES (human-paced -> ~0 CPU).
nc.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: nil) { note in
    if let a = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
       let b = a.bundleIdentifier { emit("activate \(b) \(categoryOf(a))") }
}
// Push: a GUI app launched (novelty signal; headless processes need Endpoint Security — out of scope).
nc.addObserver(forName: NSWorkspace.didLaunchApplicationNotification, object: nil, queue: nil) { note in
    if let a = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
       let b = a.bundleIdentifier { emit("launch \(b) \(categoryOf(a))") }
}

// Coarse idle, low frequency (active/idle + breaks). Not an event tap -> no Accessibility prompt.
Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { _ in
    emit(String(format: "idle %.0f", idleSeconds()))
}
// Self-terminate if orphaned (parent exited -> reparented to launchd, pid 1).
Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { _ in
    if getppid() == 1 { exit(0) }
}

// ── Actuator: read 'hide <bundle>' commands from stdin WITHOUT blocking the run loop ──
// readabilityHandler fires on FileHandle's own serial queue (never the main run loop), so the
// NSWorkspace push stream keeps flowing while we wait for Python. The AppKit hide() is dispatched
// to main. NSRunningApplication.hide() is permissionless (no TCC, no root) — reversible by the user.
func handleCommand(_ line: String) {
    let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
    guard parts.count == 2, parts[0] == "hide" else { return }
    for running in NSRunningApplication.runningApplications(withBundleIdentifier: parts[1]) {
        running.hide()
    }
}

nonisolated(unsafe) var inbuf = Data()  // accessed only on FileHandle's serial handler queue
FileHandle.standardInput.readabilityHandler = { fh in
    let chunk = fh.availableData
    if chunk.isEmpty { exit(0) }  // parent closed stdin -> exit
    inbuf.append(chunk)
    while let nl = inbuf.firstIndex(of: 0x0A) {
        let lineData = inbuf.subdata(in: inbuf.startIndex..<nl)
        inbuf.removeSubrange(inbuf.startIndex...nl)
        if let s = String(data: lineData, encoding: .utf8) {
            let cmd = s.trimmingCharacters(in: .whitespacesAndNewlines)
            DispatchQueue.main.async { handleCommand(cmd) }
        }
    }
}

emit("ready")
app.run()
