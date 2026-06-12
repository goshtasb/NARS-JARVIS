// Cognitive Identity — the passive mirror (design handoff). Calm, privacy-forward: a Mirror card ("What
// I've noticed about your computer use") with per-app bars + the always-present privacy line, then the
// learned habits and persona rows (Active/Learning pills + Forget). Strictly a view over the socket
// ("usage" / "habits" / "persona_list"); Forget routes through the daemon (DB + ONA severed together).
import AppKit

final class HabitsViewController: NSViewController {
    weak var client: JarvisClient?
    private let column = NSStackView()
    private var scroll: NSScrollView!
    private var refreshing = false

    override func loadView() {
        let root = NSView(); root.wantsLayer = true; root.layer?.backgroundColor = DS.contentBG.cgColor
        let title = DS.text("Cognitive Identity", 19, .bold, DS.label)
        title.translatesAutoresizingMaskIntoConstraints = false
        let refreshBtn = DSButton(nil, symbol: "arrow.clockwise", variant: .icon) { [weak self] in self?.refresh() }
        refreshBtn.translatesAutoresizingMaskIntoConstraints = false

        scroll = NSScrollView(); scroll.drawsBackground = false; scroll.hasVerticalScroller = true
        scroll.translatesAutoresizingMaskIntoConstraints = false
        column.orientation = .vertical; column.alignment = .leading; column.spacing = 16
        column.edgeInsets = NSEdgeInsets(top: 8, left: 0, bottom: 24, right: 0)
        column.translatesAutoresizingMaskIntoConstraints = false
        let flip = FlippedClip(); flip.translatesAutoresizingMaskIntoConstraints = false
        flip.addSubview(column); scroll.documentView = flip

        root.addSubview(title); root.addSubview(refreshBtn); root.addSubview(scroll)
        NSLayoutConstraint.activate([
            title.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 24),
            title.topAnchor.constraint(equalTo: root.topAnchor, constant: 18),
            refreshBtn.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -24),
            refreshBtn.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            scroll.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 12),
            scroll.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            flip.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            flip.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
            flip.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            column.topAnchor.constraint(equalTo: flip.topAnchor),
            column.centerXAnchor.constraint(equalTo: flip.centerXAnchor),
            column.widthAnchor.constraint(lessThanOrEqualToConstant: 680),
            column.widthAnchor.constraint(equalTo: flip.widthAnchor, constant: -48).withPriority(.defaultHigh),
            column.bottomAnchor.constraint(equalTo: flip.bottomAnchor),
        ])
        self.view = root
    }

    override func viewDidAppear() { super.viewDidAppear(); refresh() }

    /// Headless layout preview (offline).
    func previewSeed() {
        let mirror = """
        What I've noticed about your computer use (3 hours, 108 app switches):
        - Most of your time: Cursor (2 h 10 m), Chrome (1 h 5 m), Terminal (8 m)
        - By kind: development, communication, media
        - Busiest around: 3 PM
        (Learned passively from which app is in front — never your screen contents.)
        """
        let habits = [habitRow(["description": "You open Slack around 9 AM most weekdays", "state": "armed", "key": "h1"]),
                      habitRow(["description": "You switch to Spotify after lunch", "state": "learning", "key": "h2"])]
        let persona = [personaRow(["phrase": "Prefers terse answers with code first", "state": "Active", "term": "p1"])]
        rebuild(mirror: mirror, habits: habits, persona: persona)
    }

    @objc func refresh() {
        guard !refreshing else { return }
        refreshing = true
        var mirror = "", habits: [NSView] = [], persona: [NSView] = []
        let group = DispatchGroup()
        group.enter(); client?.call("usage", "7") { _, b in mirror = (b["text"] as? String) ?? ""; group.leave() }
        group.enter(); client?.call("habits") { [weak self] _, b in
            let rows = (b["rows"] as? [[String: Any]]) ?? []
            habits = rows.compactMap { self?.habitRow($0) }; group.leave() }
        group.enter(); client?.call("persona_list") { [weak self] _, b in
            let rows = (b["rows"] as? [[String: Any]]) ?? []
            persona = rows.compactMap { self?.personaRow($0) }; group.leave() }
        group.notify(queue: .main) { [weak self] in
            self?.refreshing = false
            self?.rebuild(mirror: mirror, habits: habits, persona: persona)
        }
    }

    private func rebuild(mirror: String, habits: [NSView], persona: [NSView]) {
        column.arrangedSubviews.forEach { $0.removeFromSuperview() }
        column.addArrangedSubview(mirrorCard(mirror))
        column.addArrangedSubview(DS.sectionHeader("Learned habits — when & where you act"))
        if habits.isEmpty { column.addArrangedSubview(DS.text("No habits yet — JARVIS learns as you repeat actions.", 12, .regular, DS.label3)) }
        else { habits.forEach { column.addArrangedSubview($0) } }
        column.addArrangedSubview(DS.sectionHeader("Style & preferences"))
        if persona.isEmpty { column.addArrangedSubview(DS.text("No style learned yet — JARVIS infers it as you work.", 12, .regular, DS.label3)) }
        else { persona.forEach { column.addArrangedSubview($0) } }
        column.arrangedSubviews.forEach { v in
            v.widthAnchor.constraint(equalTo: column.widthAnchor).isActive = true
        }
    }

    // ── the Mirror card ──
    private func mirrorCard(_ text: String) -> NSView {
        let card = DS.rounded(bg: DS.card, radius: 14, border: DS.separator)
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 10
        col.translatesAutoresizingMaskIntoConstraints = false
        let parsed = parse(text)
        if parsed.empty {
            let tile = DS.iconTile("sparkles", tint: DS.accent, side: 40, pt: 18)
            col.addArrangedSubview(tile)
            col.addArrangedSubview(DS.text("Still learning", 15, .semibold, DS.label))
            col.addArrangedSubview(DS.text("Give me a couple more days of normal use — I learn from which app is in front.", 12.5, .regular, DS.label2, wrap: true))
        } else {
            let eye = DS.iconTile("eye", tint: DS.accent, side: 30, pt: 14)
            let headCol = NSStackView(views: [DS.text("What I've noticed about your computer use", 14, .semibold, DS.label),
                                              DS.text(parsed.meta, 11.5, .regular, DS.label2)])
            headCol.orientation = .vertical; headCol.alignment = .leading; headCol.spacing = 1
            let head = NSStackView(views: [eye, headCol]); head.orientation = .horizontal; head.spacing = 9; head.alignment = .top
            col.addArrangedSubview(head)
            if !parsed.apps.isEmpty {
                col.addArrangedSubview(DS.sectionHeader("Most of your time"))
                let maxV = parsed.apps.map { $0.1 }.max() ?? 1
                for (name, mins) in parsed.apps { col.addArrangedSubview(appBar(name, mins, maxV)) }
            }
            for line in parsed.extra { col.addArrangedSubview(DS.text(line, 12.5, .regular, DS.label2, wrap: true)) }
        }
        let sep = DS.rounded(bg: DS.separator, radius: 0)
        let privacy = DS.text("Learned from which app is in front — never your screen contents.", 11.5, .regular, DS.label3, wrap: true)
        col.addArrangedSubview(sep); col.addArrangedSubview(privacy)
        card.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            col.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            col.topAnchor.constraint(equalTo: card.topAnchor, constant: 14),
            col.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -14),
            sep.heightAnchor.constraint(equalToConstant: 0.5), sep.widthAnchor.constraint(equalTo: col.widthAnchor),
        ])
        return card
    }

    private func appBar(_ name: String, _ mins: Int, _ maxV: Int) -> NSView {
        let label = DS.text(name, 12.5, .medium, DS.label)
        label.setContentHuggingPriority(.required, for: .horizontal)
        let track = DS.rounded(bg: DS.fill(0.08), radius: 4)
        let fill = DS.rounded(bg: DS.accent, radius: 4)
        track.addSubview(fill)
        let dur = DS.text(fmtMins(mins), 11.5, .regular, DS.label2, mono: true)
        let stack = NSStackView(views: [label, track, dur]); stack.orientation = .horizontal; stack.spacing = 10; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            label.widthAnchor.constraint(equalToConstant: 120),
            track.heightAnchor.constraint(equalToConstant: 8),
            fill.topAnchor.constraint(equalTo: track.topAnchor), fill.bottomAnchor.constraint(equalTo: track.bottomAnchor),
            fill.leadingAnchor.constraint(equalTo: track.leadingAnchor),
            fill.widthAnchor.constraint(equalTo: track.widthAnchor, multiplier: max(0.04, CGFloat(mins) / CGFloat(max(1, maxV)))),
        ])
        return stack
    }

    // ── habit / persona rows ──
    private func habitRow(_ r: [String: Any]) -> NSView {
        let desc = r["description"] as? String ?? "(habit)"
        let armed = (r["state"] as? String) == "armed"
        return idRow(text: desc, tag: armed ? .active : .learning, id: r["key"] as? String ?? "", cmd: "habit_forget")
    }
    private func personaRow(_ r: [String: Any]) -> NSView {
        let phrase = r["phrase"] as? String ?? "(constraint)"
        let active = (r["state"] as? String) == "Active"
        return idRow(text: phrase, tag: active ? .active : .learning, id: r["term"] as? String ?? "", cmd: "persona_forget")
    }
    private enum Tag { case active, learning }
    private func idRow(text: String, tag: Tag, id: String, cmd: String) -> NSView {
        let card = DS.rounded(bg: DS.card, radius: 10, border: DS.separator)
        let pill = tag == .active ? DS.pill("Active", symbol: nil, color: DS.green) : DS.pill("Learning", symbol: nil, color: DS.amber)
        let label = DS.text(text, 12.5, .regular, DS.label, wrap: true)
        let forget = DSButton("Forget", variant: .destructive, size: 11.5) { [weak self] in
            self?.client?.call(cmd, id) { _, _ in DispatchQueue.main.async { self?.refresh() } }
        }
        let st = NSStackView(views: [pill, label, NSView(), forget])
        st.orientation = .horizontal; st.spacing = 9; st.alignment = .centerY
        st.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(st)
        NSLayoutConstraint.activate([
            st.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 12),
            st.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -10),
            st.topAnchor.constraint(equalTo: card.topAnchor, constant: 9),
            st.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -9),
        ])
        return card
    }

    // ── parse the daemon's text summary into structured pieces for the card ──
    private struct Parsed { var empty = true; var meta = ""; var apps: [(String, Int)] = []; var extra: [String] = [] }
    private func parse(_ text: String) -> Parsed {
        var p = Parsed()
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return p }
        p.empty = false
        for raw in t.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("What I've noticed") {
                if let lo = line.firstIndex(of: "("), let hi = line.firstIndex(of: ")") { p.meta = String(line[line.index(after: lo)..<hi]) }
            } else if line.hasPrefix("- Most of your time:") {
                let body = line.replacingOccurrences(of: "- Most of your time:", with: "")
                p.apps = body.split(separator: ",").compactMap { seg in
                    let s = seg.trimmingCharacters(in: .whitespaces)
                    guard let lo = s.firstIndex(of: "("), let hi = s.lastIndex(of: ")") else { return nil }
                    let name = String(s[..<lo]).trimmingCharacters(in: .whitespaces)
                    let mins = parseMins(String(s[s.index(after: lo)..<hi]))
                    return mins > 0 ? (name, mins) : nil
                }
            } else if line.hasPrefix("- ") {
                p.extra.append(String(line.dropFirst(2)))
            }
        }
        return p
    }
    private func parseMins(_ s: String) -> Int {
        var m = 0
        if let h = s.range(of: #"(\d+)\s*h"#, options: .regularExpression) { m += (Int(s[h].filter(\.isNumber)) ?? 0) * 60 }
        if let mm = s.range(of: #"(\d+)\s*m"#, options: .regularExpression) { m += Int(s[mm].filter(\.isNumber)) ?? 0 }
        return m
    }
    private func fmtMins(_ m: Int) -> String { m >= 60 ? (m % 60 == 0 ? "\(m/60) h" : "\(m/60) h \(m%60) m") : "\(m) m" }
}

extension NSLayoutConstraint {
    func withPriority(_ p: NSLayoutConstraint.Priority) -> NSLayoutConstraint { priority = p; return self }
}
