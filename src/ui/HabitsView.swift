// The Habit Brain dashboard (ADR-030): a glanceable menu-bar list of every learned tendency/habit
// with its live [Armed]/[Learning] state, plus a one-click Forget. Strictly a view — it fetches the
// structured snapshot over the socket ("habits") and routes Forget back through the daemon
// ("habit_forget") so the ONA term is cratered, never a raw DB write. Zero NARS math lives here: the
// daemon hands over finished `state`/`seen` strings (the UI never sees frequency/confidence). It is
// the telemetry instrument for the field test. Mirrors ChatView's thin-client style. See service/README.
import AppKit

final class HabitsViewController: NSViewController {
    weak var client: JarvisClient?
    private let stack = NSStackView()
    private let status = NSTextField(labelWithString: "")

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 360))

        let title = NSTextField(labelWithString: "🧠 Habits JARVIS is learning")
        title.frame = NSRect(x: 12, y: 332, width: 396, height: 18)
        title.font = .boldSystemFont(ofSize: 13)
        title.textColor = .secondaryLabelColor

        let refreshBtn = NSButton(title: "↻", target: self, action: #selector(refresh))
        refreshBtn.frame = NSRect(x: 384, y: 330, width: 26, height: 22)
        refreshBtn.bezelStyle = .rounded
        refreshBtn.toolTip = "Refresh"

        let scroll = NSScrollView(frame: NSRect(x: 8, y: 8, width: 404, height: 316))
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

        status.frame = NSRect(x: 14, y: 300, width: 380, height: 16)
        status.font = .systemFont(ofSize: 11)
        status.textColor = .tertiaryLabelColor

        container.addSubview(title)
        container.addSubview(refreshBtn)
        container.addSubview(scroll)
        container.addSubview(status)
        self.view = container
    }

    /// Pull the latest snapshot from the daemon (fetch-on-open; habits change on a daily timescale,
    /// so no polling is needed). Safe to call repeatedly.
    @objc func refresh() {
        status.stringValue = "loading…"
        client?.call("habits") { [weak self] _, body in
            let rows = (body["rows"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.render(rows) }
        }
    }

    private func render(_ rows: [[String: Any]]) {
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if rows.isEmpty {
            status.stringValue = "Nothing learned yet — JARVIS forms habits as you repeat actions."
            return
        }
        status.stringValue = "\(rows.count) tracked"
        for r in rows { stack.addArrangedSubview(makeRow(r)) }
    }

    private func makeRow(_ r: [String: Any]) -> NSView {
        let desc = r["description"] as? String ?? "(habit)"
        let key = r["key"] as? String ?? ""
        let armed = (r["state"] as? String) == "armed"
        let scope = r["scope"] as? String ?? "tendency"
        let seen = r["seen"] as? Int ?? 0
        let arms = r["arms_at"] as? Int ?? 0
        let badge = armed ? "🟢 Armed" : "🟡 Learning · seen ~\(seen)× (arms at ~\(arms))"

        let text = NSTextField(labelWithString: "\(desc)\n\(badge) · \(scope)")
        text.font = .systemFont(ofSize: 11)
        text.lineBreakMode = .byWordWrapping
        text.maximumNumberOfLines = 2
        text.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let forget = NSButton(title: "Forget", target: self, action: #selector(forgetClicked(_:)))
        forget.bezelStyle = .rounded
        forget.controlSize = .small
        forget.identifier = NSUserInterfaceItemIdentifier(key)   // carry the row key on the button
        forget.setContentHuggingPriority(.required, for: .horizontal)

        let row = NSStackView(views: [text, forget])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false
        row.widthAnchor.constraint(equalToConstant: 380).isActive = true
        return row
    }

    @objc private func forgetClicked(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue, !key.isEmpty else { return }
        sender.isEnabled = false
        // Routes through HabitLoop.forget on the daemon: craters the ONA term AND purges the row.
        client?.call("habit_forget", key) { [weak self] _, _ in
            DispatchQueue.main.async { self?.refresh() }
        }
    }
}
