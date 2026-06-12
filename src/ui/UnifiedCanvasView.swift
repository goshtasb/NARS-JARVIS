// Canvas — the task board / monitor (design handoff). NOT a composer: composing happens in Chat, so the
// old click-to-build palette is gone. A segmented header (Now · Scheduled · Activity) over one Task Row
// component rendered in all six states from the live overnight_status, with the result panel, the
// failed-row recovery trio (Retry · Change tool · Edit target), and the scheduled sleep disclaimer.
import AppKit

final class UnifiedCanvasViewController: NSViewController {
    weak var client: JarvisClient?
    private let seg = NSSegmentedControl(labels: ["Now", "Scheduled", "Activity"],
                                         trackingMode: .selectOne, target: nil, action: nil)
    private let list = NSStackView()
    private var listScroll: NSScrollView!
    private var clearBtn: DSButton!
    private var pollTimer: Timer?
    private var sub = 0      // 0 Now · 1 Scheduled · 2 Activity

    override func loadView() {
        let root = LayerView(); root.wantsLayer = true; root.bg = DS.contentBG; root.layer?.backgroundColor = DS.contentBG.cgColor
        seg.selectedSegment = 0; seg.target = self; seg.action = #selector(subChanged)
        seg.translatesAutoresizingMaskIntoConstraints = false
        seg.segmentStyle = .texturedRounded
        clearBtn = DSButton("Clear completed", variant: .secondary, size: 12) { [weak self] in
            self?.client?.call("briefing_dismiss_done") { _, _ in DispatchQueue.main.async { self?.refresh() } }
        }
        clearBtn.translatesAutoresizingMaskIntoConstraints = false; clearBtn.isHidden = true

        listScroll = NSScrollView(); listScroll.drawsBackground = false; listScroll.hasVerticalScroller = true
        listScroll.translatesAutoresizingMaskIntoConstraints = false
        list.orientation = .vertical; list.alignment = .leading; list.spacing = 10
        list.edgeInsets = NSEdgeInsets(top: 16, left: 24, bottom: 24, right: 24)
        list.translatesAutoresizingMaskIntoConstraints = false
        let flip = FlippedClip(); flip.translatesAutoresizingMaskIntoConstraints = false
        flip.addSubview(list); listScroll.documentView = flip

        root.addSubview(seg); root.addSubview(clearBtn); root.addSubview(listScroll)
        NSLayoutConstraint.activate([
            seg.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            seg.topAnchor.constraint(equalTo: root.topAnchor, constant: 14),
            clearBtn.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -24),
            clearBtn.centerYAnchor.constraint(equalTo: seg.centerYAnchor),
            listScroll.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            listScroll.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            listScroll.topAnchor.constraint(equalTo: seg.bottomAnchor, constant: 12),
            listScroll.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            flip.leadingAnchor.constraint(equalTo: listScroll.contentView.leadingAnchor),
            flip.trailingAnchor.constraint(equalTo: listScroll.contentView.trailingAnchor),
            flip.topAnchor.constraint(equalTo: listScroll.contentView.topAnchor),
            list.leadingAnchor.constraint(equalTo: flip.leadingAnchor),
            list.trailingAnchor.constraint(equalTo: flip.trailingAnchor),
            list.topAnchor.constraint(equalTo: flip.topAnchor),
            list.bottomAnchor.constraint(equalTo: flip.bottomAnchor),
            list.widthAnchor.constraint(equalTo: listScroll.widthAnchor),
        ])
        self.view = root
    }

    override func viewDidAppear() { super.viewDidAppear(); refresh(); startPolling() }
    override func viewDidDisappear() { super.viewDidDisappear(); pollTimer?.invalidate(); pollTimer = nil }
    func onOvernightEvent() { DispatchQueue.main.async { [weak self] in self?.refresh() } }
    private func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in self?.refresh() }
    }
    @objc private func subChanged() { sub = seg.selectedSegment; clearBtn.isHidden = (sub != 2); refresh() }

    func refresh() {
        client?.call("overnight_status") { [weak self] _, body in
            let rows = (body["rows"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.render(rows) }
        }
    }

    /// Headless layout preview (offline) — render representative rows without a daemon.
    func previewSeed(_ which: Int) {
        seg.selectedSegment = which; sub = which; clearBtn.isHidden = (which != 2)
        let nowRows: [[String: Any]] = [
            ["action": "summarize_file", "arg": "/Users/me/Q3-PRD.pdf", "status": "running", "result": "summarizing… chunk 7/12"],
            ["action": "find_file", "arg": "notes", "status": "pending"],
        ]
        let activityRows: [[String: Any]] = [
            ["action": "report_system", "arg": "", "status": "done", "result": "System report:\n- CPU: 17%\n- Memory: 70% used\n- Disk: 7% used"],
            ["action": "read_article", "arg": "/Users/me/PRD.pdf", "status": "failed", "result": "[ERROR: \"/Users/me/PRD.pdf\" is a local file, not a web page — try Summarize a document.]"],
            ["action": "empty_trash", "arg": "", "status": "held", "id": 1],
        ]
        let schedRows: [[String: Any]] = [
            ["action": "report_system", "arg": "", "status": "pending", "run_at": Date().timeIntervalSince1970 + 6*3600 + 40*60],
        ]
        render(which == 0 ? nowRows : (which == 1 ? schedRows : activityRows))
    }

    private func render(_ all: [[String: Any]]) {
        let now = Date().timeIntervalSince1970
        func runAt(_ r: [String: Any]) -> Double? { r["run_at"] as? Double }
        let items: [[String: Any]]
        let empty: String
        switch sub {
        case 1:
            items = all.filter { (runAt($0) ?? 0) > 0 && ($0["status"] as? String) == "pending" }
                       .sorted { (runAt($0) ?? 0) < (runAt($1) ?? 0) }
            empty = "Nothing scheduled."
        case 2:
            items = all.filter { ["done", "failed", "held"].contains($0["status"] as? String ?? "") }
            empty = "No finished tasks yet."
        default:
            items = all.filter { runAt($0) == nil && ["pending", "running"].contains($0["status"] as? String ?? "") }
            empty = "Nothing running.  Start a job from Chat and watch it here."
        }
        list.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let rows: [NSView] = items.isEmpty ? [emptyRow(empty)] : items.map { taskRow($0, now: now) }
        for r in rows {
            list.addArrangedSubview(r)
            r.widthAnchor.constraint(equalTo: list.widthAnchor, constant: -48).isActive = true   // after add
        }
    }

    private func emptyRow(_ s: String) -> NSView {
        let t = DS.text(s, 13, .regular, DS.label3)
        let wrap = NSView(); wrap.translatesAutoresizingMaskIntoConstraints = false
        wrap.addSubview(t)
        NSLayoutConstraint.activate([
            wrap.heightAnchor.constraint(equalToConstant: 120),
            t.centerXAnchor.constraint(equalTo: wrap.centerXAnchor),
            t.centerYAnchor.constraint(equalTo: wrap.centerYAnchor),
        ])
        return wrap
    }

    // ── the Task Row (all six states) ──
    private func taskRow(_ r: [String: Any], now: Double) -> NSView {
        let action = r["action"] as? String ?? "?"
        let arg = r["arg"] as? String ?? ""
        var state = r["status"] as? String ?? "pending"
        let result = r["result"] as? String ?? ""
        let runAt = r["run_at"] as? Double
        if state == "pending" && (runAt ?? 0) > now { state = "scheduled" }
        let chunk = parseChunk(result)
        if state == "running" && chunk != nil { state = "working" }

        let card = DS.rounded(bg: DS.card, radius: 11, border: DS.separator)
        card.translatesAutoresizingMaskIntoConstraints = false
        let glyph = DS.symbol(DS.stateGlyph(state), 17, .medium, DS.stateColor(state))
        // title: "action — target" (target monospaced/secondary)
        let title = NSStackView(); title.orientation = .horizontal; title.spacing = 6; title.alignment = .firstBaseline
        title.addArrangedSubview(DS.text(action, 13.5, .semibold, DS.label))
        if !arg.isEmpty {
            title.addArrangedSubview(DS.text("—", 12.5, .regular, DS.label3))
            title.addArrangedSubview(DS.text((arg as NSString).lastPathComponent, 12, .regular, DS.label2, mono: true))
        }
        let badge = DS.stateBadge(state)
        let head = NSStackView(views: [glyph, title, NSView(), badge])
        head.orientation = .horizontal; head.spacing = 10; head.alignment = .centerY
        head.translatesAutoresizingMaskIntoConstraints = false

        let col = NSStackView(views: [head]); col.orientation = .vertical; col.alignment = .leading; col.spacing = 8
        col.translatesAutoresizingMaskIntoConstraints = false

        switch state {
        case "working":
            if let (i, n) = chunk { col.addArrangedSubview(progressBar(Double(i) / Double(max(1, n)))) ; col.addArrangedSubview(DS.text("chunk \(i) / \(n)", 12, .regular, DS.label2, mono: true)) }
        case "running":
            col.addArrangedSubview(progressBar(nil))
        case "done":
            if !result.isEmpty { col.addArrangedSubview(resultPanel(result)) }
        case "failed":
            if !result.isEmpty { col.addArrangedSubview(DS.text(result, 12, .regular, DS.red, wrap: true, selectable: true)) }
            col.addArrangedSubview(recoveryBar(action: action, arg: arg))
        case "held":
            col.addArrangedSubview(DS.text("Needs your approval — it can change your system.", 12, .regular, DS.label2))
            col.addArrangedSubview(heldBar(id: r["id"] as? Int ?? -1))
        case "scheduled":
            let when = runAt.map { fmtTime($0) } ?? ""
            col.addArrangedSubview(DS.text("\(when)   ·   \(countdown((runAt ?? now) - now))", 12, .regular, DS.amber))
            col.addArrangedSubview(disclaimer())
        default:
            col.addArrangedSubview(DS.text("Waiting to start", 12, .regular, DS.label3))
        }

        head.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true
        card.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 13),
            col.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -13),
            col.topAnchor.constraint(equalTo: card.topAnchor, constant: 11),
            col.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -11),
        ])
        return card
    }

    private func progressBar(_ frac: Double?) -> NSView {
        let track = DS.rounded(bg: DS.fill(0.10), radius: 3)
        let fill = DS.rounded(bg: DS.blue, radius: 3)
        track.addSubview(fill)
        track.heightAnchor.constraint(equalToConstant: 6).isActive = true
        track.widthAnchor.constraint(equalToConstant: 360).isActive = true
        fill.topAnchor.constraint(equalTo: track.topAnchor).isActive = true
        fill.bottomAnchor.constraint(equalTo: track.bottomAnchor).isActive = true
        fill.leadingAnchor.constraint(equalTo: track.leadingAnchor).isActive = true
        if let frac { fill.widthAnchor.constraint(equalTo: track.widthAnchor, multiplier: max(0.02, min(1, frac))).isActive = true }
        else { fill.widthAnchor.constraint(equalTo: track.widthAnchor, multiplier: 0.38).isActive = true }
        return track
    }

    private func resultPanel(_ text: String) -> NSView {
        let panel = DS.rounded(bg: DS.contentBG, radius: 8, border: DS.separator)
        let header = DS.sectionHeader("Result")
        let copy = DSButton("Copy", variant: .quiet, size: 11) {
            NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
        }
        let hrow = NSStackView(views: [header, NSView(), copy]); hrow.orientation = .horizontal; hrow.alignment = .centerY
        let body = DS.text(text, 12, .regular, DS.label, wrap: true, mono: true, selectable: true)
        let col = NSStackView(views: [hrow, body]); col.orientation = .vertical; col.alignment = .leading; col.spacing = 5
        col.translatesAutoresizingMaskIntoConstraints = false
        panel.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 10),
            col.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -10),
            col.topAnchor.constraint(equalTo: panel.topAnchor, constant: 8),
            col.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -8),
            hrow.widthAnchor.constraint(equalTo: col.widthAnchor),
            body.widthAnchor.constraint(lessThanOrEqualToConstant: 560),
        ])
        return panel
    }

    private func recoveryBar(action: String, arg: String) -> NSView {
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 5
        let row = NSStackView(); row.orientation = .horizontal; row.spacing = 8
        row.addArrangedSubview(DSButton("Retry", symbol: "arrow.clockwise", variant: .secondary, size: 12) { [weak self] in self?.requeue(action, arg) })
        var change: DSButton!
        change = DSButton("Change tool", symbol: "arrow.left.arrow.right", variant: .secondary, size: 12) { [weak self, weak change] in
            guard let self, let anchor = change else { return }
            self.client?.call("action_alternatives", ["action": action, "arg": arg]) { _, b in
                let alts = (b["alternatives"] as? [[String: Any]]) ?? []
                DispatchQueue.main.async { self.popAlternatives(alts, arg: arg, anchor: anchor) }
            }
        }
        row.addArrangedSubview(change)
        col.addArrangedSubview(row)
        if !arg.isEmpty {
            let field = NSTextField(string: arg)
            field.font = DS.mono(12); field.translatesAutoresizingMaskIntoConstraints = false
            field.widthAnchor.constraint(equalToConstant: 380).isActive = true
            let rerun = DSButton("Re-run", variant: .primary, size: 12) { [weak self, weak field] in
                let fixed = field?.stringValue.trimmingCharacters(in: .whitespaces) ?? ""
                if !fixed.isEmpty { self?.requeue(action, fixed) }
            }
            let er = NSStackView(views: [field, rerun]); er.orientation = .horizontal; er.spacing = 6
            col.addArrangedSubview(er)
        }
        return col
    }
    private func popAlternatives(_ alts: [[String: Any]], arg: String, anchor: NSView) {
        let menu = NSMenu()
        if alts.isEmpty { let i = NSMenuItem(title: "No alternative for this input", action: nil, keyEquivalent: ""); i.isEnabled = false; menu.addItem(i) }
        else { for a in alts { let n = a["name"] as? String ?? "?"; let l = a["label"] as? String ?? n
            menu.addItem(ClosureMenuItem(title: "\(n) — \(l)") { [weak self] in self?.requeue(n, arg) }) } }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: anchor.bounds.height + 4), in: anchor)
    }
    private func requeue(_ action: String, _ arg: String) {
        client?.call("overnight_enqueue_batch", [["action": action, "arg": arg]]) { [weak self] _, _ in
            self?.client?.call("overnight_start") { _, _ in }
            DispatchQueue.main.async { self?.seg.selectedSegment = 0; self?.subChanged() }
        }
    }
    private func heldBar(id: Int) -> NSView {
        let row = NSStackView(); row.orientation = .horizontal; row.spacing = 8
        row.addArrangedSubview(DSButton("Deny", variant: .secondary, size: 12) { [weak self] in self?.resolveHeld(id, false) })
        let ok = DSButton("Approve & run", variant: .pillAccent, size: 12) { [weak self] in self?.resolveHeld(id, true) }
        ok.layer?.backgroundColor = DS.green.cgColor
        row.addArrangedSubview(ok)
        return row
    }
    private func resolveHeld(_ id: Int, _ ok: Bool) {
        client?.call("briefing_resolve", ["id": id, "accepted": ok]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.refresh() }
        }
    }
    private func disclaimer() -> NSView {
        let bar = DS.rounded(bg: DS.amber.withAlphaComponent(0.14), radius: 7)
        let t = DS.text("Runs at the set time if your Mac is awake — otherwise on the next wake. Nothing runs while it's off.",
                        11.5, .regular, DS.amber, wrap: true)
        bar.addSubview(t)
        NSLayoutConstraint.activate([
            t.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 9),
            t.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -9),
            t.topAnchor.constraint(equalTo: bar.topAnchor, constant: 6),
            t.bottomAnchor.constraint(equalTo: bar.bottomAnchor, constant: -6),
            t.widthAnchor.constraint(lessThanOrEqualToConstant: 520),
        ])
        return bar
    }

    private func parseChunk(_ s: String) -> (Int, Int)? {
        guard let r = s.range(of: #"chunk (\d+)/(\d+)"#, options: .regularExpression) else { return nil }
        let nums = s[r].split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
        return nums.count == 2 ? (nums[0], nums[1]) : nil
    }
    private func countdown(_ secs: Double) -> String {
        if secs <= 0 { return "due now" }
        let h = Int(secs) / 3600, m = (Int(secs) % 3600) / 60
        return h > 0 ? "in \(h) h \(m) m" : "in \(m) m"
    }
    private func fmtTime(_ epoch: Double) -> String {
        let f = DateFormatter(); f.dateFormat = "h:mm a"; return f.string(from: Date(timeIntervalSince1970: epoch))
    }
}

/// A menu item that runs a closure (the Change-tool dropdown).
final class ClosureMenuItem: NSMenuItem {
    private var handler: (() -> Void)?
    convenience init(title: String, handler: @escaping () -> Void) {
        self.init(title: title, action: #selector(fire), keyEquivalent: "")
        self.handler = handler; self.target = self
    }
    @objc private func fire() { handler?() }
}
