// Entry point for the menu-bar app. .accessory => menu-bar only, no Dock icon (the LSUIElement
// posture carried over from the sensor agent). All behavior lives in AppDelegate.
import AppKit

// Headless self-check (CI / agent verification): connect to the daemon, round-trip one request, exit
// WITHOUT starting the GUI run loop. Proves the app binary links and its IPC works on machines with
// no window-server session.
if CommandLine.arguments.contains("--check") {
    let path = ProcessInfo.processInfo.environment["NARS_JARVIS_SOCK"]
        ?? "\(NSTemporaryDirectory())nars-jarvis.sock"
    guard let c = JarvisClient(path: path) else { print("CHECK-FAIL: connect"); exit(1) }
    c.start()
    guard let (ok, body) = c.callSync("status") else { print("CHECK-FAIL: timeout"); exit(2) }
    print("CHECK-OK: status ok=\(ok) \(body)")
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
