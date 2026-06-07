// The popover's content: a scrollable transcript + a one-line input. Strictly a view — it sends the
// typed command to the daemon via JarvisClient and renders the reply. Zero reasoning, zero state
// beyond the transcript text. Chat scope (per Phase 2): learn / ask / tell (+ status/health); a
// bare line is treated as a question. Actions/interventions arrive as native notifications.
import AppKit

final class ChatViewController: NSViewController {
    weak var client: JarvisClient?
    private let transcript = NSTextView()
    private let input = NSTextField()
    private static let known = ["learn", "ask", "tell", "status", "health"]

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 320))
        let scroll = NSScrollView(frame: NSRect(x: 8, y: 40, width: 404, height: 272))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        transcript.isEditable = false
        transcript.isVerticallyResizable = true
        transcript.autoresizingMask = [.width]
        transcript.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        scroll.documentView = transcript
        input.frame = NSRect(x: 8, y: 8, width: 404, height: 24)
        input.placeholderString = "learn / ask / tell …"
        input.target = self
        input.action = #selector(submit)
        container.addSubview(scroll)
        container.addSubview(input)
        self.view = container
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
