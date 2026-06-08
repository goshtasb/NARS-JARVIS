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

        let scroll = NSScrollView(frame: NSRect(x: 8, y: 40, width: 404, height: 240))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        transcript.isEditable = false
        transcript.isVerticallyResizable = true
        transcript.autoresizingMask = [.width]
        transcript.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        scroll.documentView = transcript
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
        container.addSubview(input)
        container.addSubview(voiceButton)
        self.view = container
    }

    @objc private func quit() { onQuit?() }
    @objc private func stopAll() { onStop?() }
    @objc private func toggleVoice() { onToggleVoice?() }

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
        let known = Self.known.contains(parts[0])
        let cmd = known ? parts[0] : "ask"                       // bare text -> a question
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
