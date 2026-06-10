// The Batch Canvas (ADR-033): a dedicated window to compose an overnight batch. Left = a palette of
// actions fetched from `catalog_schema` (each tagged Autonomous/Held by the daemon — the UI hardcodes
// NO business logic). Center = the plan you build by clicking palette buttons; each row carries an
// argument field, the daemon's tag, and a remove ×. Commit sends the whole array to
// `overnight_enqueue_batch`. Strictly a dumb client — it renders whatever the daemon returns. Click-to-
// add (not drag-drop): same outcome, far less AppKit surface. Mirrors HabitsView/MorningBriefingView.
import AppKit

final class BatchCanvasViewController: NSViewController {
    weak var client: JarvisClient?
    private let palette = NSStackView()
    private let plan = NSStackView()
    private let status = NSTextField(labelWithString: "")
    private var rows: [PlanRow] = []

    // One composed task: the action + its (file picker / text) argument field + the daemon's tag.
    private final class PlanRow {
        let name: String
        let takesArg: Bool
        let autonomous: Bool
        let field = NSTextField()
        let view = NSStackView()
        init(name: String, takesArg: Bool, autonomous: Bool) {
            self.name = name; self.takesArg = takesArg; self.autonomous = autonomous
        }
    }

    override func loadView() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 820, height: 560))

        let title = NSTextField(labelWithString: "🗂 Overnight Batch Canvas")
        title.frame = NSRect(x: 16, y: 524, width: 500, height: 22)
        title.font = .boldSystemFont(ofSize: 15)

        // ── left: the action palette (from catalog_schema) ──
        let palLabel = NSTextField(labelWithString: "Actions")
        palLabel.frame = NSRect(x: 16, y: 496, width: 220, height: 18)
        palLabel.font = .boldSystemFont(ofSize: 12); palLabel.textColor = .secondaryLabelColor
        let palScroll = NSScrollView(frame: NSRect(x: 16, y: 56, width: 240, height: 432))
        palScroll.hasVerticalScroller = true; palScroll.borderType = .bezelBorder; palScroll.drawsBackground = false
        palette.orientation = .vertical; palette.alignment = .leading; palette.spacing = 6
        palette.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        palette.translatesAutoresizingMaskIntoConstraints = false
        palScroll.documentView = palette
        NSLayoutConstraint.activate([
            palette.leadingAnchor.constraint(equalTo: palScroll.contentView.leadingAnchor),
            palette.trailingAnchor.constraint(equalTo: palScroll.contentView.trailingAnchor),
            palette.topAnchor.constraint(equalTo: palScroll.contentView.topAnchor),
        ])

        // ── center: the plan you compose ──
        let planLabel = NSTextField(labelWithString: "Plan (runs in order; Held actions wait for morning approval)")
        planLabel.frame = NSRect(x: 272, y: 496, width: 532, height: 18)
        planLabel.font = .boldSystemFont(ofSize: 12); planLabel.textColor = .secondaryLabelColor
        let planScroll = NSScrollView(frame: NSRect(x: 272, y: 56, width: 532, height: 432))
        planScroll.hasVerticalScroller = true; planScroll.borderType = .bezelBorder; planScroll.drawsBackground = false
        plan.orientation = .vertical; plan.alignment = .leading; plan.spacing = 6
        plan.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        plan.translatesAutoresizingMaskIntoConstraints = false
        planScroll.documentView = plan
        NSLayoutConstraint.activate([
            plan.leadingAnchor.constraint(equalTo: planScroll.contentView.leadingAnchor),
            plan.trailingAnchor.constraint(equalTo: planScroll.contentView.trailingAnchor),
            plan.topAnchor.constraint(equalTo: planScroll.contentView.topAnchor),
        ])

        // ── bottom bar ──
        let commit = NSButton(title: "Commit Queue", target: self, action: #selector(commit))
        commit.frame = NSRect(x: 272, y: 14, width: 130, height: 30); commit.bezelColor = .systemGreen
        commit.keyEquivalent = "\r"
        let startBtn = NSButton(title: "Commit + Start", target: self, action: #selector(commitAndStart))
        startBtn.frame = NSRect(x: 408, y: 14, width: 140, height: 30)
        status.frame = NSRect(x: 560, y: 20, width: 244, height: 18)
        status.font = .systemFont(ofSize: 11); status.textColor = .tertiaryLabelColor

        for v in [title, palLabel, palScroll, planLabel, planScroll, commit, startBtn, status] {
            container.addSubview(v)
        }
        self.view = container
    }

    override func viewDidAppear() { super.viewDidAppear(); refresh() }

    func refresh() {
        client?.call("catalog_schema") { [weak self] _, body in
            let actions = (body["actions"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.renderPalette(actions) }
        }
    }

    private func renderPalette(_ actions: [[String: Any]]) {
        palette.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for a in actions {
            let name = a["name"] as? String ?? "?"
            let auto = a["autonomous"] as? Bool ?? false
            let takesArg = a["takes_arg"] as? Bool ?? false
            let b = NSButton(title: "\(auto ? "🟢" : "🟠") \(name)", target: self, action: #selector(addBlock(_:)))
            b.bezelStyle = .rounded; b.alignment = .left
            b.toolTip = (a["label"] as? String ?? "") + (auto ? "  ·  Autonomous" : "  ·  Held for approval")
            // pack the metadata onto the button so the click handler can build the row
            b.identifier = NSUserInterfaceItemIdentifier("\(name)|\(takesArg ? 1 : 0)|\(auto ? 1 : 0)")
            b.widthAnchor.constraint(equalToConstant: 216).isActive = true
            palette.addArrangedSubview(b)
        }
    }

    @objc private func addBlock(_ sender: NSButton) {
        let parts = (sender.identifier?.rawValue ?? "").split(separator: "|", omittingEmptySubsequences: false)
        guard parts.count == 3 else { return }
        let row = PlanRow(name: String(parts[0]), takesArg: parts[1] == "1", autonomous: parts[2] == "1")

        let badge = NSTextField(labelWithString: row.autonomous ? "Autonomous" : "Held")
        badge.font = .boldSystemFont(ofSize: 10)
        badge.textColor = row.autonomous ? .systemGreen : .systemOrange
        let nameLabel = NSTextField(labelWithString: row.name)
        nameLabel.font = .systemFont(ofSize: 12)
        nameLabel.widthAnchor.constraint(equalToConstant: 130).isActive = true

        var views: [NSView] = [badge, nameLabel]
        if row.takesArg {
            row.field.placeholderString = "argument…"
            row.field.widthAnchor.constraint(equalToConstant: 200).isActive = true
            views.append(row.field)
            let choose = NSButton(title: "Choose…", target: self, action: #selector(chooseFile(_:)))
            choose.bezelStyle = .rounded; choose.controlSize = .small
            choose.identifier = NSUserInterfaceItemIdentifier(row.name + "#\(rows.count)")
            views.append(choose)
        }
        let remove = NSButton(title: "×", target: self, action: #selector(removeBlock(_:)))
        remove.bezelStyle = .rounded; remove.controlSize = .small
        remove.identifier = NSUserInterfaceItemIdentifier("\(rows.count)")
        views.append(remove)

        row.view.setViews(views, in: .leading)
        row.view.orientation = .horizontal; row.view.alignment = .centerY; row.view.spacing = 8
        row.view.translatesAutoresizingMaskIntoConstraints = false
        row.view.widthAnchor.constraint(equalToConstant: 512).isActive = true
        rows.append(row)
        plan.addArrangedSubview(row.view)
        status.stringValue = "\(rows.count) block(s)"
    }

    @objc private func chooseFile(_ sender: NSButton) {
        guard let raw = sender.identifier?.rawValue, let idx = Int(raw.split(separator: "#").last ?? ""),
              idx < rows.count else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            rows[idx].field.stringValue = url.path
        }
    }

    @objc private func removeBlock(_ sender: NSButton) {
        guard let raw = sender.identifier?.rawValue, let idx = Int(raw), idx < rows.count else { return }
        plan.arrangedSubviews[idx].removeFromSuperview()
        rows.remove(at: idx)
        rebuildPlan()                          // re-index remaining rows' identifiers
    }

    private func rebuildPlan() {
        let snapshot = rows
        rows = []
        plan.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for r in snapshot { reAdd(r) }
        status.stringValue = "\(rows.count) block(s)"
    }

    private func reAdd(_ r: PlanRow) {
        // re-append an existing row preserving its typed argument (re-indexes button identifiers)
        let saved = r.field.stringValue
        let synth = NSButton()
        synth.identifier = NSUserInterfaceItemIdentifier("\(r.name)|\(r.takesArg ? 1 : 0)|\(r.autonomous ? 1 : 0)")
        addBlock(synth)
        rows.last?.field.stringValue = saved
    }

    private func batchPayload() -> [[String: Any]] {
        rows.map { ["action": $0.name, "arg": $0.takesArg ? $0.field.stringValue : ""] }
    }

    @objc private func commit() { send(start: false) }
    @objc private func commitAndStart() { send(start: true) }

    private func send(start: Bool) {
        let payload = batchPayload()
        guard !payload.isEmpty else { status.stringValue = "add a block first"; return }
        client?.call("overnight_enqueue_batch", payload) { [weak self] _, body in
            let queued = body["queued"] as? Int ?? 0
            DispatchQueue.main.async {
                self?.status.stringValue = (body["text"] as? String) ?? "committed \(queued)"
                if start { self?.client?.call("overnight_start") { _, _ in } }
                self?.rows.removeAll()
                self?.plan.arrangedSubviews.forEach { $0.removeFromSuperview() }
            }
        }
    }
}
