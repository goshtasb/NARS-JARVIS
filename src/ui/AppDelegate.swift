// The menu-bar app delegate: owns the NSStatusItem + popover, the JarvisClient, and native
// notifications. Strictly a thin client — it forwards keystrokes to the daemon and renders events.
// Sentinel alerts and intervention prompts arrive as daemon events and are surfaced via
// UNUserNotificationCenter (replacing the old osascript hack); a notification action button replies
// to the daemon's intervention without the user opening the popover.
import AppKit
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private let chat = ChatViewController()
    private var client: JarvisClient?
    private let recorder = AudioRecorder()
    private var failsafe: Timer?                 // force-stops a runaway recording
    private static let maxRecordSeconds = 30.0

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "🔵 JARVIS"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(statusClick)
        statusItem.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])  // right-click -> quit menu
        popover.behavior = .transient
        popover.contentViewController = chat
        popover.contentSize = NSSize(width: 420, height: 320)
        chat.onQuit = { NSApp.terminate(nil) }
        chat.onStop = { [weak self] in self?.emergencyStop() }

        let path = ProcessInfo.processInfo.environment["NARS_JARVIS_SOCK"]
            ?? "\(NSTemporaryDirectory())nars-jarvis.sock"
        guard let c = JarvisClient(path: path) else {
            _log("UI: could not connect to daemon at \(path)")
            chat.append("⚠ could not connect to JARVIS daemon at \(path)")
            return
        }
        c.onEvent = { [weak self] kind, body in
            DispatchQueue.main.async { self?.handleEvent(kind, body) }
        }
        c.start()
        client = c
        chat.client = c
        _log("UI: connected to daemon at \(path)")
        setupNotifications()
        setupVoice()
    }

    // ── push-to-talk: ⌥Space (hold) -> record -> send WAV path on release ──
    private func setupVoice() {
        AudioRecorder.requestPermission()
        HotKey.shared.onPressed = { [weak self] in self?.startRecording() }
        HotKey.shared.onReleased = { [weak self] in self?.stopAndSend() }
        if HotKey.shared.register() {
            chat.append("🎙 push-to-talk ready — hold ⌥Space to speak.")
        } else {
            chat.append("⚠ could not register the ⌥Space hotkey.")
        }
    }

    private func startRecording() {
        guard !recorder.isRecording else { return }
        recorder.start()
        statusItem.button?.title = "🔴 JARVIS"
        // Failsafe: if the release event is swallowed (cmd-tab, interrupt), never run away.
        failsafe = Timer.scheduledTimer(withTimeInterval: Self.maxRecordSeconds, repeats: false) {
            [weak self] _ in self?.stopAndSend()
        }
    }

    private func stopAndSend() {
        failsafe?.invalidate(); failsafe = nil
        guard let path = recorder.stop() else { return }
        statusItem.button?.title = "🔵 JARVIS"
        client?.call("voice", ["path": path]) { _, _ in }   // transcript/answer arrive as events
    }

    @objc private func statusClick() {
        // Right-click -> a quit menu (so you can stop JARVIS without opening the chat). Left -> popover.
        if NSApp.currentEvent?.type == .rightMouseUp {
            let menu = NSMenu()
            menu.addItem(NSMenuItem(title: "Open JARVIS", action: #selector(openPopover), keyEquivalent: ""))
            menu.addItem(.separator())
            let stop = NSMenuItem(title: "⛔ Emergency Stop (quit everything)",
                                  action: #selector(emergencyStop), keyEquivalent: "")
            menu.addItem(stop)
            menu.addItem(NSMenuItem(title: "Quit JARVIS (UI only)",
                                    action: #selector(quitApp), keyEquivalent: "q"))
            for item in menu.items { item.target = self }
            statusItem.menu = menu
            statusItem.button?.performClick(nil)   // present the menu
            statusItem.menu = nil                  // detach so left-click still opens the popover
        } else {
            openPopover()
        }
    }

    @objc private func openPopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            chat.focusInput()
        }
    }

    @objc private func quitApp() { NSApp.terminate(nil) }

    /// Kill switch: tell the daemon to shut down (stops the brains, sentinel, autonomy, voice), then
    /// quit the UI. The one action that turns the WHOLE system off.
    @objc private func emergencyStop() {
        client?.call("shutdown") { _, _ in }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
    }

    private func handleEvent(_ kind: String, _ body: [String: Any]) {
        switch kind {
        case "intervention":
            let id = body["id"] as? Int ?? -1
            let prompt = body["prompt"] as? String ?? "Focus intervention"
            chat.append("⚠ " + prompt)
            notifyIntervention(id: id, prompt: prompt)
        case "acted":                                        // the Sentinel acted autonomously (earned trust)
            let id = body["id"] as? Int ?? -1
            let text = body["text"] as? String ?? "Acted autonomously."
            chat.append("🤖 " + text)
            notifyActed(id: id, text: text)
        case "transcript":                                   // what whisper heard (the daemon speaks the reply)
            chat.append("🎙 " + (body["text"] as? String ?? ""))
        case "answer":
            chat.append((body["text"] as? String ?? ""))
        default:                                             // "alert" (sentinel / system)
            let text = body["text"] as? String ?? ""
            chat.append(text)
            notify(title: "NARS-JARVIS", text: text)
        }
    }

    // ── native notifications (replaces the dropped osascript banner) ──
    private func setupNotifications() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let hide = UNNotificationAction(identifier: "HIDE", title: "Hide apps", options: [])
        let dismiss = UNNotificationAction(identifier: "DISMISS", title: "Not now", options: [.destructive])
        let intervention = UNNotificationCategory(
            identifier: "INTERVENTION", actions: [hide, dismiss], intentIdentifiers: [], options: [])
        // Autonomous action already happened -> offer Undo (revokes trust) / Keep.
        let undo = UNNotificationAction(identifier: "UNDO", title: "Undo", options: [.destructive])
        let keep = UNNotificationAction(identifier: "KEEP", title: "Keep", options: [])
        let acted = UNNotificationCategory(
            identifier: "ACTED", actions: [undo, keep], intentIdentifiers: [], options: [])
        center.setNotificationCategories([intervention, acted])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func notify(title: String, text: String) {
        guard !text.isEmpty else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = text
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    private func notifyIntervention(id: Int, prompt: String) {
        postNotification(id: id, identifier: "intv-\(id)", title: "Focus",
                         body: prompt, category: "INTERVENTION")
    }

    private func notifyActed(id: Int, text: String) {
        postNotification(id: id, identifier: "acted-\(id)", title: "JARVIS acted",
                         body: text, category: "ACTED")
    }

    private func postNotification(id: Int, identifier: String, title: String, body: String, category: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.categoryIdentifier = category
        content.userInfo = ["id": id]
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
    }

    // Tapping a notification action replies to the daemon. HIDE/KEEP -> accepted; DISMISS/UNDO -> not.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completion: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["id"] as? Int ?? -1
        let accepted = ["HIDE", "KEEP"].contains(response.actionIdentifier)
        client?.call("intervene", ["id": id, "accepted": accepted]) { [weak self] _, body in
            DispatchQueue.main.async { self?.chat.append(body["text"] as? String ?? "") }
        }
        completion()
    }

    // Show banners even while the app is frontmost.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completion: @escaping (UNNotificationPresentationOptions) -> Void) {
        completion([.banner, .sound])
    }

    private func _log(_ s: String) {
        FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
    }
}
