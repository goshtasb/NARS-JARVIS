// The Morning Briefing (ADR-031): what JARVIS finished overnight, and the actions it HELD for your
// approval. Strictly a view — it fetches the briefing over the socket ("briefing") and routes each
// approve/deny back through the daemon ("briefing_resolve"), where an approval is the literal consent
// gate (the daemon runs the held action on accept). No autonomy lives here. Mirrors HabitsView's thin-
// client style. The consumption end of the overnight engine; the production end is the overnight_* cmds.
import AppKit

final class MorningBriefingViewController: NSViewController {
    weak var client: JarvisClient?
    private let stack = NSStackView()
    private let status = NSTextField(labelWithString: "")

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 440, height: 380))

        let title = NSTextField(labelWithString: "🌅 Morning Briefing")
        title.frame = NSRect(x: 12, y: 352, width: 416, height: 18)
        title.font = .boldSystemFont(ofSize: 13)
        title.textColor = .secondaryLabelColor

        let refreshBtn = NSButton(title: "↻", target: self, action: #selector(refresh))
        refreshBtn.frame = NSRect(x: 404, y: 350, width: 26, height: 22)
        refreshBtn.bezelStyle = .rounded

        let scroll = NSScrollView(frame: NSRect(x: 8, y: 8, width: 424, height: 336))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.drawsBackground = false

        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 10, left: 10, bottom: 10, right: 10)
        stack.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = stack
        let clip = scroll.contentView
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: clip.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: clip.trailingAnchor),
            stack.topAnchor.constraint(equalTo: clip.topAnchor),
        ])

        status.frame = NSRect(x: 14, y: 322, width: 400, height: 16)
        status.font = .systemFont(ofSize: 11)
        status.textColor = .tertiaryLabelColor

        container.addSubview(title)
        container.addSubview(refreshBtn)
        container.addSubview(scroll)
        container.addSubview(status)
        self.view = container
    }

    @objc func refresh() {
        status.stringValue = "loading…"
        client?.call("briefing") { [weak self] _, body in
            let done = (body["done"] as? [[String: Any]]) ?? []
            let held = (body["held"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.render(done: done, held: held) }
        }
    }

    private func render(done: [[String: Any]], held: [[String: Any]]) {
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        status.stringValue = "\(done.count) done · \(held.count) awaiting approval"

        stack.addArrangedSubview(sectionLabel("✅ Completed overnight"))
        if done.isEmpty {
            stack.addArrangedSubview(dimLabel("Nothing ran — queue something with overnight_enqueue."))
        } else {
            for d in done { stack.addArrangedSubview(doneRow(d)) }
        }

        stack.addArrangedSubview(sectionLabel("⏸ Held for your approval"))
        if held.isEmpty {
            stack.addArrangedSubview(dimLabel("No actions are waiting."))
        } else {
            for h in held { stack.addArrangedSubview(heldRow(h)) }
        }
    }

    private func sectionLabel(_ s: String) -> NSView {
        let l = NSTextField(labelWithString: s)
        l.font = .boldSystemFont(ofSize: 12)
        return l
    }

    private func dimLabel(_ s: String) -> NSView {
        let l = NSTextField(labelWithString: s)
        l.font = .systemFont(ofSize: 11)
        l.textColor = .tertiaryLabelColor
        return l
    }

    private func doneRow(_ d: [String: Any]) -> NSView {
        let action = d["action"] as? String ?? "(task)"
        let arg = d["arg"] as? String ?? ""
        let failed = (d["status"] as? String) == "failed"
        let mark = failed ? "✗" : "•"
        let l = NSTextField(labelWithString: "\(mark) \(action) \(arg)".trimmingCharacters(in: .whitespaces))
        l.font = .systemFont(ofSize: 11)
        l.textColor = failed ? .systemRed : .labelColor
        l.lineBreakMode = .byTruncatingTail
        return l
    }

    private func heldRow(_ h: [String: Any]) -> NSView {
        let id = h["id"] as? Int ?? -1
        let action = h["action"] as? String ?? "(action)"
        let arg = h["arg"] as? String ?? ""
        let reason = h["reason"] as? String ?? ""
        let text = NSTextField(labelWithString: "\(action) \(arg)\n\(reason)".trimmingCharacters(in: .whitespaces))
        text.font = .systemFont(ofSize: 11)
        text.lineBreakMode = .byWordWrapping
        text.maximumNumberOfLines = 2
        text.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let approve = NSButton(title: "Approve", target: self, action: #selector(approve(_:)))
        approve.bezelStyle = .rounded; approve.controlSize = .small
        approve.bezelColor = .systemGreen
        approve.identifier = NSUserInterfaceItemIdentifier(String(id))
        approve.setContentHuggingPriority(.required, for: .horizontal)

        let deny = NSButton(title: "Deny", target: self, action: #selector(deny(_:)))
        deny.bezelStyle = .rounded; deny.controlSize = .small
        deny.identifier = NSUserInterfaceItemIdentifier(String(id))
        deny.setContentHuggingPriority(.required, for: .horizontal)

        let row = NSStackView(views: [text, approve, deny])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 6
        row.translatesAutoresizingMaskIntoConstraints = false
        row.widthAnchor.constraint(equalToConstant: 400).isActive = true
        return row
    }

    @objc private func approve(_ sender: NSButton) { resolve(sender, accepted: true) }
    @objc private func deny(_ sender: NSButton) { resolve(sender, accepted: false) }

    private func resolve(_ sender: NSButton, accepted: Bool) {
        guard let raw = sender.identifier?.rawValue, let id = Int(raw) else { return }
        sender.isEnabled = false
        // The daemon runs the held action on approval — this click IS the consent gate (ADR-031).
        client?.call("briefing_resolve", ["id": id, "accepted": accepted]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.refresh() }
        }
    }
}
