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
    private var sockPath = ""                     // ADR-017: remembered for auto-reconnect
    private let recorder = AudioRecorder()
    private var failsafe: Timer?                 // force-stops a runaway recording
    private static let maxRecordSeconds = 30.0
    // ADR-020 interactive consent: which consent ids currently have a card, and their local TTL
    // timers (so a card self-dismisses offline at the daemon-supplied deadline).
    private var liveConsents: Set<Int> = []
    private var consentTimers: [Int: Timer] = [:]
    // ADR-021 GUI actuation: the latest focused-window AX snapshot (the id->element map lives here,
    // in the app, never on the wire), and a monotonically increasing epoch.
    private var axSnapshot: AXSnapshot?
    private var axEpoch = 0

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
        sockPath = path
        setupNotifications()
        setupVoice()
        setupAX()                   // ADR-021: watch focus changes, serialize the focused window
        connect(reconnect: false)   // ADR-017: retry until up, and auto-reconnect on a daemon restart
    }

    // ── ADR-021: GUI actuation — eyes (serialize focused window) + hands (actuate on approval) ──
    private func setupAX() {
        AXPermission.requestIfNeeded()   // surfaces JARVIS in the Accessibility list for one-time grant
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(appActivated(_:)),
            name: NSWorkspace.didActivateApplicationNotification, object: nil)
    }

    @objc private func appActivated(_ note: Notification) {
        guard let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
              app.bundleIdentifier != Bundle.main.bundleIdentifier,   // skip ourselves
              AXPermission.trusted() else { return }
        axEpoch += 1
        let snap = AXSerializer.serialize(pid: app.processIdentifier, epoch: axEpoch)
        axSnapshot = snap
        client?.call("ax_context", ["epoch": snap.epoch, "dom": snap.dom, "ids": snap.ids]) { _, _ in }
    }

    // ── ADR-017: resilient connect — retry with backoff, survive daemon restarts ──
    private func connect(reconnect: Bool) {
        if reconnect { DispatchQueue.main.async { [weak self] in self?.setConnected(false) } }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            var delay = 0.5
            while true {
                if let c = JarvisClient(path: self.sockPath) {
                    DispatchQueue.main.async { self.wire(c, reconnect: reconnect) }
                    return
                }
                Thread.sleep(forTimeInterval: delay)
                delay = min(delay * 2, 5.0)            // capped backoff
            }
        }
    }

    private func wire(_ c: JarvisClient, reconnect: Bool) {   // main thread
        c.onEvent = { [weak self] kind, body in
            DispatchQueue.main.async { self?.handleEvent(kind, body) }
        }
        c.onDisconnect = { [weak self] in
            DispatchQueue.main.async {
                self?.chat.append("⚠ lost the daemon — reconnecting…")
                self?.client?.close()
                self?.connect(reconnect: true)
            }
        }
        c.start()
        client = c
        chat.client = c
        setConnected(true)
        chat.append(reconnect ? "↻ reconnected to JARVIS." : "✓ connected to JARVIS.")
        _log("UI: \(reconnect ? "reconnected" : "connected") to daemon at \(sockPath)")
    }

    private func setConnected(_ up: Bool) {            // main thread — reflect IPC state in the menu bar
        statusItem?.button?.title = up ? "🔵 JARVIS" : "⚪ JARVIS"
    }

    // ── push-to-talk: click-to-toggle from the menu-bar popover (no global hotkey -> no conflicts) ──
    private func setupVoice() {
        AudioRecorder.requestPermission()
        chat.onToggleVoice = { [weak self] in self?.toggleVoice() }
        chat.append("🎙 click 'Listen' to talk; click again (or it auto-stops at 30s) to send.")
    }

    private func toggleVoice() {
        if recorder.isRecording {
            stopAndSend()
        } else {
            startRecording()
        }
    }

    private func startRecording() {
        guard !recorder.isRecording else { return }
        recorder.start()
        statusItem.button?.title = "🔴 JARVIS"
        chat.setRecording(true)
        // Failsafe: a toggle left on never runs away — auto-stop and send after 30s.
        failsafe = Timer.scheduledTimer(withTimeInterval: Self.maxRecordSeconds, repeats: false) {
            [weak self] _ in self?.stopAndSend()
        }
    }

    private func stopAndSend() {
        failsafe?.invalidate(); failsafe = nil
        chat.setRecording(false)
        statusItem.button?.title = "🔵 JARVIS"
        guard let path = recorder.stop() else { return }
        chat.append("… transcribing")
        client?.call("voice", ["path": path]) { [weak self] ok, body in
            // success -> transcript/answer arrive as events; failure -> surface it (no more silent dead button)
            if !ok, let msg = body["text"] as? String {
                DispatchQueue.main.async { self?.chat.append("⚠ " + msg) }
            }
        }
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
        case "consent_request":                              // ADR-020: unified Approve/Deny prompt
            consentRequested(body)
        case "consent_closed":                               // resolved/expired -> withdraw the card
            withdrawConsent(body["id"] as? Int ?? -1)
        case "consent_sync":                                 // (re)connect -> reconcile against the server
            reconcileConsents(body)
        case "actuate":                                      // ADR-021: an approved GUI action to perform
            let (ok, detail) = AXActuator.actuate(
                snapshot: axSnapshot,
                epoch: body["epoch"] as? Int ?? -1,
                id: body["id"] as? String ?? "",
                verb: body["verb"] as? String ?? "",
                args: body["args"] as? [String: Any] ?? [:])
            chat.append((ok ? "🤖 " : "⚠ ") + detail)
            client?.call("ax_result", ["id": body["id"] as? String ?? "", "ok": ok, "detail": detail]) { _, _ in }
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
        // ADR-020: a generic Approve/Deny category for any unified consent request (the prompt text
        // carries the specifics). Deny is destructive-styled so the safe choice reads as such.
        let approve = UNNotificationAction(identifier: "APPROVE", title: "Approve", options: [])
        let deny = UNNotificationAction(identifier: "DENY", title: "Deny", options: [.destructive])
        let consent = UNNotificationCategory(
            identifier: "CONSENT", actions: [approve, deny], intentIdentifiers: [], options: [])
        center.setNotificationCategories([intervention, acted, consent])
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

    // ── ADR-020 interactive consent ──
    /// Render a consent card and arm a LOCAL TTL timer so it self-dismisses even with zero
    /// connectivity. The deadline is computed as a duration (expires_at − server_now) to dodge clock skew.
    private func consentRequested(_ body: [String: Any]) {
        let id = body["id"] as? Int ?? -1
        guard id >= 0 else { return }
        let prompt = (body["prompt"] as? String) ?? (body["label"] as? String) ?? "Approve this action?"
        chat.append("⏳ " + prompt)
        liveConsents.insert(id)
        postNotification(id: id, identifier: "consent-\(id)", title: "JARVIS needs your OK",
                         body: prompt, category: "CONSENT")
        if let expires = body["expires_at"] as? Double, let now = body["server_now"] as? Double {
            armConsentTTL(id, after: max(0, expires - now))
        }
    }

    private func armConsentTTL(_ id: Int, after seconds: Double) {
        consentTimers[id]?.invalidate()
        consentTimers[id] = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) {
            [weak self] _ in self?.withdrawConsent(id)       // offline self-dismiss (server is authoritative)
        }
    }

    /// Remove a consent card + its timer. Idempotent — safe for closed/expired/synced-away ids.
    private func withdrawConsent(_ id: Int) {
        guard id >= 0 else { return }
        liveConsents.remove(id)
        consentTimers.removeValue(forKey: id)?.invalidate()
        let center = UNUserNotificationCenter.current()
        center.removeDeliveredNotifications(withIdentifiers: ["consent-\(id)"])
        center.removePendingNotificationRequests(withIdentifiers: ["consent-\(id)"])
    }

    /// Reconcile our cards against the daemon's authoritative open-set (on reconnect): drop any card
    /// the server no longer holds (already expired/resolved while we were away), render any we're
    /// missing. This is what makes a permanently-hung card structurally impossible.
    private func reconcileConsents(_ body: [String: Any]) {
        let requests = body["requests"] as? [[String: Any]] ?? []
        let serverNow = body["server_now"] as? Double
        let openIds = Set(requests.compactMap { $0["id"] as? Int })
        for stale in liveConsents.subtracting(openIds) { withdrawConsent(stale) }
        for var req in requests where !liveConsents.contains((req["id"] as? Int) ?? -1) {
            if req["server_now"] == nil, let serverNow { req["server_now"] = serverNow }  // arm TTL
            consentRequested(req)
        }
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

    // Tapping a notification action replies to the daemon. For unified consent (ADR-020): APPROVE ->
    // accepted, DENY -> not, via `consent_resolve`. Legacy Sentinel auto-mode: HIDE/KEEP -> accepted,
    // DISMISS/UNDO -> not, via `intervene`.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completion: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["id"] as? Int ?? -1
        let action = response.actionIdentifier
        let isConsent = response.notification.request.content.categoryIdentifier == "CONSENT"
        if isConsent {
            withdrawConsent(id)                              // clear local card + TTL immediately
            let accepted = (action == "APPROVE")            // default tap / DENY -> not accepted
            client?.call("consent_resolve", ["id": id, "accepted": accepted]) { [weak self] _, body in
                DispatchQueue.main.async { self?.chat.append(body["text"] as? String ?? "") }
            }
        } else {
            let accepted = ["HIDE", "KEEP"].contains(action)
            client?.call("intervene", ["id": id, "accepted": accepted]) { [weak self] _, body in
                DispatchQueue.main.async { self?.chat.append(body["text"] as? String ?? "") }
            }
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
