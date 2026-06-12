// The Unified Canvas (ADR-053) — one window, three tabs (Now / Scheduled / Activity), replacing the
// old Batch Canvas + Morning Briefing. A STRICT projection of the daemon's task state: it renders
// exactly what overnight_status / overnight_progress report (no guessed or fake progress) and partitions
// the same rows by state — Now = the manual run you watch live, Scheduled = upcoming run_at tasks with a
// countdown, Activity = finished results + actions held for approval (with failed-task recovery).
//
// Dumb client (ADR-033): all business logic — autonomy tags, alternative tools, the run_at epoch is the
// ONE thing computed here (timezone math the daemon must not own) — comes from the daemon.
import AppKit

/// A button that runs a Swift closure — keeps per-row actions (retry, approve, change-tool) local to
/// where the row is built instead of fanning out to @objc selectors with packed identifiers.
final class ClosureButton: NSButton {
    private var handler: (() -> Void)?
    convenience init(_ title: String, handler: @escaping () -> Void) {
        self.init(title: title, target: nil, action: nil)
        self.handler = handler; self.target = self; self.action = #selector(fire)
        self.bezelStyle = .rounded; self.controlSize = .small; self.font = .systemFont(ofSize: 11)
    }
    @objc private func fire() { handler?() }
}

final class UnifiedCanvasViewController: NSViewController {
    weak var client: JarvisClient?

    private enum Tab: Int { case now = 0, scheduled = 1, activity = 2 }
    private var tab: Tab = .now

    private let tabs = NSSegmentedControl(labels: ["● Now", "Scheduled", "Activity"],
                                          trackingMode: .selectOne, target: nil, action: nil)
    private let palette = NSStackView()
    private let plan = NSStackView()          // the composer (Now + Scheduled)
    private let monitor = NSStackView()       // Now: live tasks · Scheduled: upcoming
    private let activity = NSStackView()       // Activity: done/failed/held
    private let status = NSTextField(labelWithString: "")

    private var paletteScroll: NSScrollView!
    private var planScroll: NSScrollView!
    private var monitorScroll: NSScrollView!
    private var activityScroll: NSScrollView!
    private var planLabel: NSTextField!
    private var monitorLabel: NSTextField!
    private var nowBar: NSView!
    private var schedBar: NSView!
    private var activityBar: NSView!

    private var rows: [PlanRow] = []
    private var pollTimer: Timer?

    private final class PlanRow {
        let name: String; let takesArg: Bool; let autonomous: Bool
        let field = NSTextField(); let view = NSStackView()
        init(name: String, takesArg: Bool, autonomous: Bool) {
            self.name = name; self.takesArg = takesArg; self.autonomous = autonomous
        }
    }

    // ── layout ──
    override func loadView() {
        let c = NSView(frame: NSRect(x: 0, y: 0, width: 860, height: 620))

        let title = NSTextField(labelWithString: "🗂 Canvas")
        title.frame = NSRect(x: 16, y: 588, width: 300, height: 22); title.font = .boldSystemFont(ofSize: 15)

        tabs.frame = NSRect(x: 16, y: 552, width: 360, height: 26)
        tabs.selectedSegment = 0; tabs.target = self; tabs.action = #selector(tabChanged)

        status.frame = NSRect(x: 392, y: 556, width: 452, height: 18)
        status.font = .systemFont(ofSize: 11); status.textColor = .tertiaryLabelColor
        status.alignment = .right

        let palLabel = section("Actions", x: 16, y: 528, w: 240)
        paletteScroll = scroll(palette, x: 16, y: 100, w: 240, h: 424)

        planLabel = section("Plan to run (in order)", x: 272, y: 528, w: 572)
        planScroll = scroll(plan, x: 272, y: 316, w: 572, h: 208)
        monitorLabel = section("Live", x: 272, y: 292, w: 572)
        monitorScroll = scroll(monitor, x: 272, y: 100, w: 572, h: 184)

        activityScroll = scroll(activity, x: 16, y: 100, w: 828, h: 424)

        nowBar = buildNowBar()
        schedBar = buildScheduledBar()
        activityBar = buildActivityBar()

        for v in [title, tabs, status, palLabel, paletteScroll!, planLabel!, planScroll!,
                  monitorLabel!, monitorScroll!, activityScroll!, nowBar!, schedBar!, activityBar!] {
            c.addSubview(v)
        }
        self.view = c
        applyTab()
    }

    private func section(_ s: String, x: CGFloat, y: CGFloat, w: CGFloat) -> NSTextField {
        let l = NSTextField(labelWithString: s)
        l.frame = NSRect(x: x, y: y, width: w, height: 18)
        l.font = .boldSystemFont(ofSize: 12); l.textColor = .secondaryLabelColor
        return l
    }

    private func scroll(_ doc: NSStackView,
                        x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat) -> NSScrollView {
        let s = NSScrollView(frame: NSRect(x: x, y: y, width: w, height: h))
        s.hasVerticalScroller = true; s.borderType = .bezelBorder; s.drawsBackground = false
        doc.orientation = .vertical; doc.alignment = .leading; doc.spacing = 6
        doc.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        doc.translatesAutoresizingMaskIntoConstraints = false
        s.documentView = doc
        NSLayoutConstraint.activate([
            doc.leadingAnchor.constraint(equalTo: s.contentView.leadingAnchor),
            doc.trailingAnchor.constraint(equalTo: s.contentView.trailingAnchor),
            doc.topAnchor.constraint(equalTo: s.contentView.topAnchor),
        ])
        return s
    }

    private func buildNowBar() -> NSView {
        let bar = NSView(frame: NSRect(x: 272, y: 14, width: 572, height: 76))
        let run = ClosureButton("▶ Run Now") { [weak self] in self?.runNow() }
        run.frame = NSRect(x: 0, y: 36, width: 150, height: 30)
        run.bezelColor = .systemBlue; run.controlSize = .regular; run.font = .boldSystemFont(ofSize: 13)
        run.toolTip = "Queue and run immediately (heavy work runs off the main thread, ADR-052)."
        let hint = NSTextField(wrappingLabelWithString: "Runs now; watch each task move QUEUED → RUNNING → DONE in Live below.")
        hint.frame = NSRect(x: 160, y: 36, width: 412, height: 30)
        hint.font = .systemFont(ofSize: 11); hint.textColor = .tertiaryLabelColor
        bar.addSubview(run); bar.addSubview(hint)
        return bar
    }

    private func buildScheduledBar() -> NSView {
        let bar = NSView(frame: NSRect(x: 272, y: 14, width: 572, height: 76))
        let presets: [(String, () -> Double)] = [
            ("Tonight 2 AM", { Self.tonight2am() }),
            ("In 1 hour", { Date().addingTimeInterval(3600).timeIntervalSince1970 }),
            ("In 4 hours", { Date().addingTimeInterval(4 * 3600).timeIntervalSince1970 }),
        ]
        var x: CGFloat = 0
        for (label, epoch) in presets {
            let b = ClosureButton(label) { [weak self] in self?.schedule(at: epoch()) }
            b.frame = NSRect(x: x, y: 40, width: 130, height: 28)
            b.bezelColor = .systemTeal; b.controlSize = .regular; b.font = .systemFont(ofSize: 12)
            bar.addSubview(b); x += 138
        }
        // The honest, VISIBLE constraint of a local-first daemon — never hidden behind a tooltip.
        let disclaimer = NSTextField(wrappingLabelWithString:
            "⏰ Runs at the chosen time if your Mac is awake — otherwise on the next wake. No cloud; nothing runs while powered off.")
        disclaimer.frame = NSRect(x: 0, y: 2, width: 572, height: 34)
        disclaimer.font = .systemFont(ofSize: 11); disclaimer.textColor = .systemOrange
        bar.addSubview(disclaimer)
        return bar
    }

    private func buildActivityBar() -> NSView {
        let bar = NSView(frame: NSRect(x: 16, y: 14, width: 828, height: 40))
        let clear = ClosureButton("Clear completed") { [weak self] in
            self?.client?.call("briefing_dismiss_done") { _, _ in DispatchQueue.main.async { self?.refresh() } }
        }
        clear.frame = NSRect(x: 0, y: 6, width: 150, height: 26)
        let hint = NSTextField(labelWithString: "Finished results and anything held for your approval.")
        hint.frame = NSRect(x: 160, y: 8, width: 600, height: 20)
        hint.font = .systemFont(ofSize: 11); hint.textColor = .tertiaryLabelColor
        bar.addSubview(clear); bar.addSubview(hint)
        return bar
    }

    private static func tonight2am() -> Double {
        let cal = Calendar.current; let now = Date()
        var comps = cal.dateComponents([.year, .month, .day], from: now)
        comps.hour = 2; comps.minute = 0; comps.second = 0
        var target = cal.date(from: comps) ?? now.addingTimeInterval(3600)
        if target <= now { target = cal.date(byAdding: .day, value: 1, to: target) ?? target }
        return target.timeIntervalSince1970
    }

    // ── lifecycle ──
    override func viewDidAppear() {
        super.viewDidAppear()
        refresh()
        startPolling()
    }
    override func viewDidDisappear() { super.viewDidDisappear(); pollTimer?.invalidate(); pollTimer = nil }

    /// AppDelegate forwards overnight_* socket events here for instant reaction (in addition to the
    /// 1 s poll). We just re-read authoritative state — the UI never fabricates a transition.
    func onOvernightEvent() { DispatchQueue.main.async { [weak self] in self?.refreshStatus() } }

    private func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
    }

    @objc private func tabChanged() { tab = Tab(rawValue: tabs.selectedSegment) ?? .now; applyTab(); refreshStatus() }

    private func applyTab() {
        let composing = (tab != .activity)
        paletteScroll.isHidden = !composing
        planScroll.isHidden = !composing; planLabel.isHidden = !composing
        monitorScroll.isHidden = !composing; monitorLabel.isHidden = !composing
        activityScroll.isHidden = (tab != .activity); activityBar.isHidden = (tab != .activity)
        nowBar.isHidden = (tab != .now)
        schedBar.isHidden = (tab != .scheduled)
        monitorLabel.stringValue = (tab == .scheduled) ? "Upcoming (scheduled)" : "Live"
    }

    // ── palette + composer ──
    func refresh() {
        client?.call("catalog_schema") { [weak self] _, body in
            let actions = (body["actions"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.renderPalette(actions); self?.refreshStatus() }
        }
    }

    private func renderPalette(_ actions: [[String: Any]]) {
        palette.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for a in actions {
            let name = a["name"] as? String ?? "?"
            let auto = a["autonomous"] as? Bool ?? false
            let takesArg = a["takes_arg"] as? Bool ?? false
            let b = ClosureButton("\(auto ? "🟢" : "🟠") \(name)") { [weak self] in
                self?.addRow(name: name, takesArg: takesArg, autonomous: auto)
            }
            b.alignment = .left
            b.toolTip = (a["label"] as? String ?? "") + (auto ? "  ·  Autonomous" : "  ·  Held for approval")
            b.widthAnchor.constraint(equalToConstant: 216).isActive = true
            palette.addArrangedSubview(b)
        }
    }

    private func addRow(name: String, takesArg: Bool, autonomous: Bool) {
        let row = PlanRow(name: name, takesArg: takesArg, autonomous: autonomous)
        let badge = NSTextField(labelWithString: autonomous ? "Auto" : "Held")
        badge.font = .boldSystemFont(ofSize: 10); badge.textColor = autonomous ? .systemGreen : .systemOrange
        let label = NSTextField(labelWithString: name); label.font = .systemFont(ofSize: 12)
        var views: [NSView] = [badge, label]
        if takesArg {
            row.field.placeholderString = "argument / file path"
            row.field.frame = NSRect(x: 0, y: 0, width: 240, height: 22)
            row.field.widthAnchor.constraint(equalToConstant: 240).isActive = true
            let pick = ClosureButton("📁") { [weak self] in self?.pickFile(into: row.field) }
            views.append(row.field); views.append(pick)
        }
        let remove = ClosureButton("✕") { [weak self] in
            row.view.removeFromSuperview(); self?.rows.removeAll { $0 === row }; self?.updateRunState()
        }
        views.append(remove)
        row.view.orientation = .horizontal; row.view.spacing = 6; row.view.alignment = .centerY
        views.forEach { row.view.addArrangedSubview($0) }
        rows.append(row); plan.addArrangedSubview(row.view); updateRunState()
    }

    private func pickFile(into field: NSTextField) {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { field.stringValue = url.path }
    }

    private func updateRunState() { status.stringValue = rows.isEmpty ? "" : "\(rows.count) task(s) composed" }

    private func composedItems() -> [[String: String]] {
        rows.map { ["action": $0.name, "arg": $0.takesArg ? $0.field.stringValue.trimmingCharacters(in: .whitespaces) : ""] }
    }

    // ── execute: Run Now / Schedule ──
    private func runNow() {
        let items = composedItems()
        guard !items.isEmpty else { status.stringValue = "compose a task first"; return }
        client?.call("overnight_enqueue_batch", items) { [weak self] _, _ in
            self?.client?.call("overnight_start") { _, _ in
                DispatchQueue.main.async { self?.clearComposer(); self?.refreshStatus() }
            }
        }
    }

    private func schedule(at epoch: Double) {
        let items = composedItems()
        guard !items.isEmpty else { status.stringValue = "compose a task first"; return }
        client?.call("overnight_schedule_batch", ["items": items, "run_at": epoch]) { [weak self] _, body in
            DispatchQueue.main.async {
                self?.clearComposer()
                self?.status.stringValue = (body["text"] as? String) ?? "scheduled"
                self?.refreshStatus()
            }
        }
    }

    private func clearComposer() {
        rows.forEach { $0.view.removeFromSuperview() }; rows.removeAll(); updateRunState()
    }

    // ── strict state projection ──
    private func refreshStatus() {
        client?.call("overnight_status") { [weak self] _, body in
            let rows = (body["rows"] as? [[String: Any]]) ?? []
            DispatchQueue.main.async { self?.renderState(rows) }
        }
    }

    private func renderState(_ all: [[String: Any]]) {
        let now = Date().timeIntervalSince1970
        func runAt(_ r: [String: Any]) -> Double? { r["run_at"] as? Double }
        switch tab {
        case .now:
            let live = all.filter { runAt($0) == nil && (($0["status"] as? String) == "pending" || ($0["status"] as? String) == "running") }
            fill(monitor, live, empty: "Nothing running. Compose a plan and press Run Now.")
        case .scheduled:
            let upcoming = all.filter { (runAt($0) ?? 0) > 0 && ($0["status"] as? String) == "pending" }
                .sorted { (runAt($0) ?? 0) < (runAt($1) ?? 0) }
            fill(monitor, upcoming, empty: "Nothing scheduled.", showCountdown: true, now: now)
        case .activity:
            let done = all.filter { ["done", "failed", "held"].contains($0["status"] as? String ?? "") }
            fill(activity, done, empty: "No finished tasks yet.")
        }
    }

    private func fill(_ stack: NSStackView, _ items: [[String: Any]], empty: String,
                      showCountdown: Bool = false, now: Double = 0) {
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if items.isEmpty { stack.addArrangedSubview(dim(empty)); return }
        for r in items { stack.addArrangedSubview(taskRow(r, showCountdown: showCountdown, now: now)) }
    }

    /// One row, rendered to the ADR-053 visual contract from EXACTLY the backend status (+ result).
    private func taskRow(_ r: [String: Any], showCountdown: Bool, now: Double) -> NSView {
        let action = r["action"] as? String ?? "?"
        let arg = r["arg"] as? String ?? ""
        let st = r["status"] as? String ?? "pending"
        let result = r["result"] as? String ?? ""
        let argShort = arg.isEmpty ? "" : "  (\((arg as NSString).lastPathComponent))"

        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 3
        let (glyph, color) = badge(for: st)
        var headText = "\(glyph) \(action)\(argShort)"
        if showCountdown, let ra = r["run_at"] as? Double { headText += "   ⏳ \(countdown(ra - now))" }
        let head = NSTextField(labelWithString: headText)
        head.font = .boldSystemFont(ofSize: 12); head.textColor = color
        col.addArrangedSubview(head)

        // WORKING: a DETERMINATE bar only when the backend actually reports chunk i/N — else an
        // indeterminate spinner (RUNNING). Never a fabricated percentage.
        if st == "running", let (i, n) = parseChunk(result), n > 0 {
            let bar = NSProgressIndicator()
            bar.isIndeterminate = false; bar.minValue = 0; bar.maxValue = Double(n); bar.doubleValue = Double(i)
            bar.frame = NSRect(x: 0, y: 0, width: 360, height: 12)
            bar.widthAnchor.constraint(equalToConstant: 360).isActive = true
            col.addArrangedSubview(bar)
            col.addArrangedSubview(dim("chunk \(i)/\(n)"))
        } else if st == "running" {
            let spin = NSProgressIndicator()
            spin.style = .spinning; spin.controlSize = .small; spin.isIndeterminate = true; spin.startAnimation(nil)
            col.addArrangedSubview(spin)
        }

        if (st == "done" || st == "failed"), !result.isEmpty {
            col.addArrangedSubview(resultBox(result, failed: st == "failed"))
        }
        if st == "failed" { col.addArrangedSubview(recoveryBar(action: action, arg: arg)) }
        if st == "held" {
            col.addArrangedSubview(dim("needs approval"))
            col.addArrangedSubview(heldBar(id: r["id"] as? Int ?? -1, action: action, arg: arg))
        }
        return col
    }

    private func badge(for st: String) -> (String, NSColor) {
        switch st {
        case "done":    return ("✅ done", .systemGreen)
        case "failed":  return ("❌ failed", .systemRed)
        case "running": return ("▶️ running", .systemBlue)
        case "held":    return ("⏸ held", .systemOrange)
        default:        return ("⏳ queued", .tertiaryLabelColor)
        }
    }

    /// Vector 3: the FAILED affordances — Retry (same tool) + Change tool ▾ (daemon-supplied siblings).
    private func recoveryBar(action: String, arg: String) -> NSView {
        let bar = NSStackView(); bar.orientation = .horizontal; bar.spacing = 8
        bar.addArrangedSubview(ClosureButton("↻ Retry") { [weak self] in self?.requeue(action: action, arg: arg) })
        var changeBtn: ClosureButton!
        changeBtn = ClosureButton("Change tool ▾") { [weak self, weak changeBtn] in
            guard let self, let anchor = changeBtn else { return }
            self.client?.call("action_alternatives", ["action": action, "arg": arg]) { _, body in
                let alts = (body["alternatives"] as? [[String: Any]]) ?? []
                DispatchQueue.main.async { self.popAlternatives(alts, arg: arg, anchor: anchor) }
            }
        }
        bar.addArrangedSubview(changeBtn)
        return bar
    }

    private func popAlternatives(_ alts: [[String: Any]], arg: String, anchor: NSView) {
        let menu = NSMenu()
        if alts.isEmpty {
            let item = NSMenuItem(title: "No alternative tool for this input", action: nil, keyEquivalent: "")
            item.isEnabled = false; menu.addItem(item)
        } else {
            for a in alts {
                let name = a["name"] as? String ?? "?"
                let label = a["label"] as? String ?? name
                let item = ClosureMenuItem(title: "\(name) — \(label)") { [weak self] in
                    self?.requeue(action: name, arg: arg)
                }
                menu.addItem(item)
            }
        }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: anchor.bounds.height + 4), in: anchor)
    }

    /// Re-enter QUEUED in Now WITHOUT re-selecting the file: enqueue the chosen tool against the same arg
    /// and start immediately, then jump to the Now tab so the user watches it run.
    private func requeue(action: String, arg: String) {
        client?.call("overnight_enqueue_batch", [["action": action, "arg": arg]]) { [weak self] _, _ in
            self?.client?.call("overnight_start") { _, _ in
                DispatchQueue.main.async {
                    self?.tabs.selectedSegment = 0; self?.tabChanged()
                }
            }
        }
    }

    private func heldBar(id: Int, action: String, arg: String) -> NSView {
        let bar = NSStackView(); bar.orientation = .horizontal; bar.spacing = 8
        bar.addArrangedSubview(ClosureButton("Approve") { [weak self] in self?.resolveHeld(id, true) })
        bar.addArrangedSubview(ClosureButton("Deny") { [weak self] in self?.resolveHeld(id, false) })
        return bar
    }

    private func resolveHeld(_ id: Int, _ accepted: Bool) {
        client?.call("briefing_resolve", ["id": id, "accepted": accepted]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.refresh() }
        }
    }

    // ── small view helpers ──
    private func resultBox(_ text: String, failed: Bool) -> NSView {
        let tf = NSTextField(wrappingLabelWithString: text)
        tf.isSelectable = true; tf.font = .systemFont(ofSize: 11)
        tf.textColor = failed ? .systemRed : .secondaryLabelColor
        tf.preferredMaxLayoutWidth = (tab == .activity) ? 780 : 540
        tf.drawsBackground = true; tf.backgroundColor = NSColor.textBackgroundColor.withAlphaComponent(0.5)
        tf.isBordered = true; tf.bezelStyle = .roundedBezel
        return tf
    }

    private func dim(_ s: String) -> NSView {
        let l = NSTextField(labelWithString: s); l.font = .systemFont(ofSize: 11); l.textColor = .tertiaryLabelColor
        return l
    }

    private func parseChunk(_ s: String) -> (Int, Int)? {
        // matches the worker's live status: "summarizing… chunk 3/12"
        guard let r = s.range(of: #"chunk (\d+)/(\d+)"#, options: .regularExpression) else { return nil }
        let nums = s[r].split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
        return nums.count == 2 ? (nums[0], nums[1]) : nil
    }

    private func countdown(_ secs: Double) -> String {
        if secs <= 0 { return "due" }
        let h = Int(secs) / 3600, m = (Int(secs) % 3600) / 60
        return h > 0 ? "in \(h)h \(m)m" : "in \(m)m"
    }
}

/// A menu item that runs a closure (same idea as ClosureButton, for the Change-tool dropdown).
final class ClosureMenuItem: NSMenuItem {
    private var handler: (() -> Void)?
    convenience init(title: String, handler: @escaping () -> Void) {
        self.init(title: title, action: #selector(fire), keyEquivalent: "")
        self.handler = handler; self.target = self
    }
    @objc private func fire() { handler?() }
}
