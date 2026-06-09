// The popover's content: a scrollable transcript + a one-line input. Strictly a view — it sends the
// typed command to the daemon via JarvisClient and renders the reply. Zero reasoning, zero state
// beyond the transcript text. Chat scope (per Phase 2): learn / ask / tell (+ status/health); a
// bare line is treated as a question. Actions/interventions arrive as native notifications.
import AppKit

final class ChatViewController: NSViewController {
    weak var client: JarvisClient?
    var onQuit: (() -> Void)?            // set by AppDelegate
    var onStop: (() -> Void)?            // emergency stop (kill the daemon too)
    var onToggleVoice: (() -> Void)?    // click-to-toggle push-to-talk
    private let transcript = NSTextView()
    private let input = NSTextField()
    private let voiceButton = NSButton()
    // ADR-021: inline consent (Approve/Deny) so approval never depends on a notification banner.
    private let consentBar = NSView()
    private let consentLabel = NSTextField(labelWithString: "")
    private var consentId: Int?
    var onConsent: ((Int, Bool) -> Void)?    // (consent id, approved) -> AppDelegate sends consent_resolve
    // Command words routed to the daemon as-is; anything else is treated as a question (`ask`).
    // Must track the daemon's dispatch + the console (`sentinel`/`forget`/`restore` were missing,
    // so "sentinel on" was being sent to the LLM as chat). `act` is intentionally NOT here — its
    // needs_confirm round-trip has no GUI yet; use the terminal console for actions.
    private static let known = ["learn", "ask", "tell", "status", "health",
                                "sentinel", "forget", "restore"]

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 320))

        // Control row (always visible): an unmissable off-switch.
        let stop = NSButton(title: "⛔ Stop All", target: self, action: #selector(stopAll))
        stop.frame = NSRect(x: 8, y: 288, width: 110, height: 26)
        stop.bezelColor = NSColor.systemRed
        let quit = NSButton(title: "Quit", target: self, action: #selector(quit))
        quit.frame = NSRect(x: 344, y: 288, width: 68, height: 26)
        let title = NSTextField(labelWithString: "NARS-JARVIS")
        title.frame = NSRect(x: 126, y: 292, width: 210, height: 18)
        title.alignment = .center
        title.textColor = .secondaryLabelColor

        let scroll = NSScrollView(frame: NSRect(x: 8, y: 72, width: 404, height: 208))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        transcript.isEditable = false
        transcript.isVerticallyResizable = true
        transcript.autoresizingMask = [.width]
        transcript.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        scroll.documentView = transcript

        // Inline consent bar (hidden until a consent_request arrives): "<prompt>  [Approve] [Deny]".
        consentBar.frame = NSRect(x: 8, y: 38, width: 404, height: 30)
        consentBar.wantsLayer = true
        consentBar.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.15).cgColor
        consentBar.layer?.cornerRadius = 5
        consentBar.isHidden = true
        consentLabel.frame = NSRect(x: 8, y: 6, width: 250, height: 18)
        consentLabel.font = NSFont.systemFont(ofSize: 11)
        consentLabel.lineBreakMode = .byTruncatingTail
        let approve = NSButton(title: "Approve", target: self, action: #selector(approveConsent))
        approve.frame = NSRect(x: 262, y: 2, width: 72, height: 26)
        approve.bezelColor = NSColor.systemGreen
        approve.keyEquivalent = "\r"                      // Enter approves
        let deny = NSButton(title: "Deny", target: self, action: #selector(denyConsent))
        deny.frame = NSRect(x: 336, y: 2, width: 64, height: 26)
        consentBar.addSubview(consentLabel)
        consentBar.addSubview(approve)
        consentBar.addSubview(deny)

        input.frame = NSRect(x: 8, y: 8, width: 312, height: 24)
        input.placeholderString = "learn / ask / tell …"
        input.target = self
        input.action = #selector(submit)
        voiceButton.frame = NSRect(x: 326, y: 6, width: 86, height: 28)
        voiceButton.title = "🎙 Listen"
        voiceButton.bezelStyle = .rounded
        voiceButton.target = self
        voiceButton.action = #selector(toggleVoice)
        container.addSubview(stop)
        container.addSubview(quit)
        container.addSubview(title)
        container.addSubview(scroll)
        container.addSubview(consentBar)
        container.addSubview(input)
        container.addSubview(voiceButton)
        self.view = container
    }

    @objc private func quit() { onQuit?() }
    @objc private func stopAll() { onStop?() }
    @objc private func toggleVoice() { onToggleVoice?() }

    // ── ADR-021 inline consent ──
    /// Show the Approve/Deny bar for a pending consent request.
    func showConsent(_ id: Int, _ prompt: String) {
        consentId = id
        consentLabel.stringValue = prompt
        consentBar.isHidden = false
    }
    /// Hide the bar if it's showing this id (resolved elsewhere / expired).
    func clearConsent(_ id: Int) {
        if consentId == id { consentId = nil; consentBar.isHidden = true }
    }
    @objc private func approveConsent() { resolveConsent(true) }
    @objc private func denyConsent() { resolveConsent(false) }
    private func resolveConsent(_ approved: Bool) {
        guard let id = consentId else { return }
        consentId = nil; consentBar.isHidden = true
        onConsent?(id, approved)
    }

    /// AppDelegate flips this when recording starts/stops so the button reflects state.
    func setRecording(_ on: Bool) {
        voiceButton.title = on ? "■ Stop & send" : "🎙 Listen"
        voiceButton.contentTintColor = on ? .systemRed : nil
    }

    func focusInput() { view.window?.makeFirstResponder(input) }

    func append(_ text: String) {
        guard !text.isEmpty else { return }
        transcript.string += (transcript.string.isEmpty ? "" : "\n") + text
        transcript.scrollToEndOfDocument(nil)
    }

    @objc private func submit() {
        let line = input.stringValue.trimmingCharacters(in: .whitespaces)
        guard !line.isEmpty, let client = client else { return }
        input.stringValue = ""
        append("» " + line)
        let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
        let head = parts[0].lowercased()                         // commands are case-insensitive
        let known = Self.known.contains(head)
        let cmd = known ? head : "ask"                           // bare text -> a question
        let arg = known ? (parts.count > 1 ? parts[1] : "") : line
        client.call(cmd, arg) { [weak self] ok, body in
            DispatchQueue.main.async { self?.render(cmd, ok, body) }
        }
    }

    private func render(_ cmd: String, _ ok: Bool, _ body: [String: Any]) {
        if let text = body["text"] as? String { append(text) }
        if let lines = body["lines"] as? [String] { lines.forEach { append($0) } }
        guard cmd == "learn" else { return }
        if let committed = body["committed"] as? [String], !committed.isEmpty {
            append("✓ saved: " + committed.joined(separator: " · "))
        }
        for r in body["rejects"] as? [[String: Any]] ?? [] {
            append("✗ " + (r["mirror"] as? String ?? "") + " — " + (r["reason"] as? String ?? ""))
        }
        if let esc = body["escalations"] as? [[String: Any]], !esc.isEmpty {
            append("? unsure about \(esc.count) (not auto-saved; confirm in the terminal for now)")
        }
    }
}
