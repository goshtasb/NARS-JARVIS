// The Cognitive Identity dashboard (ADR-030 + ADR-037): one pane of glass over everything JARVIS has
// learned about you — your "Routine Cadence" (the time/app Habit Brain) and your "Persona Constraints"
// (the semantic persona layer). Each row has a one-click Forget that routes through the daemon so the
// SQLite row AND the live ONA belief are severed together (never a raw DB write). Strictly a view: it
// fetches structured snapshots over the socket ("habits" / "persona_list") and routes Forget back
// ("habit_forget" / "persona_forget"). Zero reasoning/NARS-math here — the daemon hands over finished
// strings. Two fixed sub-stacks so the two async fetches render into their own regions (no race).
import AppKit

final class HabitsViewController: NSViewController {
    weak var client: JarvisClient?
    private let habitRows = NSStackView()
    private let personaRows = NSStackView()
    private let noticed = NSTextField(wrappingLabelWithString: "")   // ADR-050: "What I've noticed" mirror
    private let status = NSTextField(labelWithString: "")

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 440, height: 420))

        let title = NSTextField(labelWithString: "🧠 Cognitive Identity")
        title.frame = NSRect(x: 12, y: 392, width: 396, height: 18)
        title.font = .boldSystemFont(ofSize: 14)

        let refreshBtn = NSButton(title: "↻", target: self, action: #selector(refresh))
        refreshBtn.frame = NSRect(x: 404, y: 390, width: 26, height: 22)
        refreshBtn.bezelStyle = .rounded
        refreshBtn.toolTip = "Refresh"

        status.frame = NSRect(x: 14, y: 372, width: 410, height: 16)
        status.font = .systemFont(ofSize: 11)
        status.textColor = .tertiaryLabelColor

        let scroll = NSScrollView(frame: NSRect(x: 8, y: 8, width: 424, height: 360))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.drawsBackground = false

        for s in [habitRows, personaRows] {
            s.orientation = .vertical; s.alignment = .leading; s.spacing = 6
        }
        noticed.font = .systemFont(ofSize: 11)
        noticed.textColor = .labelColor
        noticed.preferredMaxLayoutWidth = 396
        let outer = NSStackView(views: [
            sectionLabel("What I've noticed — how you use your Mac (passive, content-blind)"), noticed,
            sectionLabel("Routine Cadence — habits (when/where you act)"), habitRows,
            sectionLabel("Persona Constraints — style/focus (how you want answers)"), personaRows,
        ])
        outer.orientation = .vertical; outer.alignment = .leading; outer.spacing = 10
        outer.edgeInsets = NSEdgeInsets(top: 10, left: 10, bottom: 10, right: 10)
        outer.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = outer
        let clip = scroll.contentView
        NSLayoutConstraint.activate([
            outer.leadingAnchor.constraint(equalTo: clip.leadingAnchor),
            outer.trailingAnchor.constraint(equalTo: clip.trailingAnchor),
            outer.topAnchor.constraint(equalTo: clip.topAnchor),
        ])

        for v in [title, refreshBtn, status, scroll] { container.addSubview(v) }
        self.view = container
    }

    @objc func refresh() {
        status.stringValue = "loading…"
        client?.call("usage", "7") { [weak self] _, body in           // ADR-050: the passive-usage mirror
            let text = (body["text"] as? String) ?? ""
            DispatchQueue.main.async { self?.noticed.stringValue = text }
        }
        client?.call("habits") { [weak self] _, body in
            let rows = (body["rows"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.fill(self?.habitRows, rows.map { self!.habitRow($0) },
                                                 empty: "No habits yet — JARVIS learns as you repeat actions.") }
        }
        client?.call("persona_list") { [weak self] _, body in
            let rows = (body["rows"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async {
                self?.fill(self?.personaRows, rows.map { self!.personaRow($0) },
                           empty: "No persona learned yet — JARVIS infers your style as you work.")
                self?.status.stringValue = ""
            }
        }
    }

    private func fill(_ stack: NSStackView?, _ rows: [NSView], empty: String) {
        guard let stack = stack else { return }
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if rows.isEmpty { stack.addArrangedSubview(dim(empty)) } else { rows.forEach { stack.addArrangedSubview($0) } }
    }

    private func sectionLabel(_ s: String) -> NSView {
        let l = NSTextField(labelWithString: s); l.font = .boldSystemFont(ofSize: 12)
        l.textColor = .secondaryLabelColor; return l
    }
    private func dim(_ s: String) -> NSView {
        let l = NSTextField(labelWithString: s); l.font = .systemFont(ofSize: 11)
        l.textColor = .tertiaryLabelColor; return l
    }

    // ── Habit row (ADR-030) ──
    private func habitRow(_ r: [String: Any]) -> NSView {
        let desc = r["description"] as? String ?? "(habit)"
        let armed = (r["state"] as? String) == "armed"
        let seen = r["seen"] as? Int ?? 0, arms = r["arms_at"] as? Int ?? 0
        let badge = armed ? "🟢 Armed" : "🟡 Learning · seen ~\(seen)× (arms at ~\(arms))"
        return row(text: "\(desc)\n\(badge)", id: r["key"] as? String ?? "",
                   action: #selector(forgetHabit(_:)), destructive: false)
    }

    // ── Persona row (ADR-037) ──
    private func personaRow(_ r: [String: Any]) -> NSView {
        let phrase = r["phrase"] as? String ?? "(constraint)"
        let active = (r["state"] as? String) == "Active"
        let badge = active ? "🟢 Active" : "🟡 Learning"
        return row(text: "\(phrase)\n\(badge)", id: r["term"] as? String ?? "",
                   action: #selector(forgetPersona(_:)), destructive: true)
    }

    private func row(text: String, id: String, action: Selector, destructive: Bool) -> NSView {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 11); label.lineBreakMode = .byWordWrapping
        label.maximumNumberOfLines = 2; label.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let forget = NSButton(title: "Forget", target: self, action: action)
        forget.bezelStyle = .rounded; forget.controlSize = .small
        if destructive { forget.bezelColor = .systemRed }
        forget.identifier = NSUserInterfaceItemIdentifier(id)
        forget.setContentHuggingPriority(.required, for: .horizontal)
        let r = NSStackView(views: [label, forget])
        r.orientation = .horizontal; r.alignment = .centerY; r.spacing = 8
        r.translatesAutoresizingMaskIntoConstraints = false
        r.widthAnchor.constraint(equalToConstant: 396).isActive = true
        return r
    }

    @objc private func forgetHabit(_ sender: NSButton) { sever(sender, cmd: "habit_forget") }
    @objc private func forgetPersona(_ sender: NSButton) { sever(sender, cmd: "persona_forget") }

    private func sever(_ sender: NSButton, cmd: String) {
        guard let id = sender.identifier?.rawValue, !id.isEmpty else { return }
        sender.isEnabled = false
        // Daemon-side: deletes the SQLite row AND craters the ONA belief — DB and reasoner stay in sync.
        client?.call(cmd, id) { [weak self] _, _ in DispatchQueue.main.async { self?.refresh() } }
    }
}
