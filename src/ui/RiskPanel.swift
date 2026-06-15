// Risk & Anomalies — the glass for the corpus-aware deviation engine (Slice 3b).
//
// A pure view builder (mirrors the DS enum): given a `deviation_scan` event body from the daemon, it
// renders one card per scanned document and, inside a populated card, one DeviationRow per finding. It
// owns NO state and NO interpretation: the daemon already decided strictness and handed us a `render`
// class per finding (strict / neutral / unrankable / qualitative), so this file only maps that class to
// AppKit styling. ActivityViewController holds the scan state and calls these builders.
//
// Contract note (factual): the baseline arrives as an AGGREGATE cohort {kind, median, n}, never a verbatim
// "corpus quote" — "your standard" is the median across n of the user's own documents, not one of them.
// `baselinePhrase` formats that aggregate for display (presentation only; it is not strictness logic).
import AppKit

enum RiskPanel {

    // ── one card per scanned document ──
    static func card(_ body: [String: Any]) -> NSView {
        let doc = body["doc"] as? String ?? "document"
        let state = body["state"] as? String ?? "pending"

        let card = DS.rounded(bg: DS.card, radius: 11, border: DS.separator, borderWidth: 0.5)
        card.translatesAutoresizingMaskIntoConstraints = false

        let glyph = DS.symbol("doc.text.magnifyingglass", 16, .medium, DS.blue)
        let head = NSStackView(views: [glyph, DS.text(doc, 13.5, .semibold, DS.label), NSView()])
        head.orientation = .horizontal; head.spacing = 8; head.alignment = .centerY

        let col = NSStackView(views: [head]); col.orientation = .vertical
        col.alignment = .leading; col.spacing = 8
        col.translatesAutoresizingMaskIntoConstraints = false
        func add(_ v: NSView) { col.addArrangedSubview(v); v.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true }

        switch state {
        case "pending":  add(pendingView(body))
        case "deferred": add(deferredView())
        case "empty":    add(emptyView())
        default:                                                        // populated
            let findings = (body["findings"] as? [[String: Any]]) ?? []
            if findings.isEmpty { add(emptyView()) } else { for f in findings { add(deviationRow(f)) } }
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

    // ── Slice 4: the persistent, non-blocking corpus-ingest banner (server-authored label) ──
    static func banner(_ p: [String: Any]) -> NSView? {
        let total = p["total"] as? Int ?? 0
        let label = p["label"] as? String ?? ""
        if total == 0 || label.isEmpty { return nil }      // nothing ingested yet -> no banner
        let done = p["done"] as? Int ?? 0
        let state = p["state"] as? String ?? "idle"
        let color = state == "ingesting" ? DS.blue : DS.green
        let card = DS.rounded(bg: color.withAlphaComponent(0.10), radius: 10,
                              border: color.withAlphaComponent(0.25), borderWidth: 0.5)
        card.translatesAutoresizingMaskIntoConstraints = false
        let glyph = DS.symbol(state == "ingesting" ? "arrow.triangle.2.circlepath" : "checkmark.seal.fill",
                              14, .medium, color)
        let head = NSStackView(views: [glyph, DS.text(label, 12, .medium, DS.label, wrap: true), NSView()])
        head.orientation = .horizontal; head.spacing = 8; head.alignment = .centerY
        let col = NSStackView(views: [head]); col.orientation = .vertical; col.alignment = .leading; col.spacing = 6
        col.translatesAutoresizingMaskIntoConstraints = false
        func add(_ v: NSView) { col.addArrangedSubview(v); v.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true }
        if state == "ingesting" { add(bar(Double(done) / Double(max(1, total)), color)) }   // determinate, no shift
        head.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true
        card.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 12),
            col.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -12),
            col.topAnchor.constraint(equalTo: card.topAnchor, constant: 10),
            col.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -10),
        ])
        return card
    }

    private static func bar(_ frac: Double, _ color: NSColor) -> NSView {
        let track = DS.rounded(bg: DS.fill(0.10), radius: 3)
        let fill = DS.rounded(bg: color, radius: 3)
        track.addSubview(fill)
        track.heightAnchor.constraint(equalToConstant: 6).isActive = true
        fill.topAnchor.constraint(equalTo: track.topAnchor).isActive = true
        fill.bottomAnchor.constraint(equalTo: track.bottomAnchor).isActive = true
        fill.leadingAnchor.constraint(equalTo: track.leadingAnchor).isActive = true
        fill.widthAnchor.constraint(equalTo: track.widthAnchor, multiplier: max(0.02, min(1, frac))).isActive = true
        return track
    }

    // ── the four progressive states ──
    static func pendingView(_ body: [String: Any]) -> NSView {
        let n = body["salient_count"] as? Int ?? 0
        let spin = NSProgressIndicator()
        spin.style = .spinning; spin.controlSize = .small
        spin.translatesAutoresizingMaskIntoConstraints = false
        spin.startAnimation(nil)
        let label = DS.text("Checking \(n) salient clause\(n == 1 ? "" : "s") against your corpus…",
                            12, .regular, DS.label2)
        let row = NSStackView(views: [spin, label, NSView()])
        row.orientation = .horizontal; row.spacing = 8; row.alignment = .centerY
        row.translatesAutoresizingMaskIntoConstraints = false
        row.heightAnchor.constraint(equalToConstant: 26).isActive = true     // reserve space -> no layout shift
        return row
    }

    static func emptyView() -> NSView {
        badgeLine("checkmark.seal.fill", DS.green, "No deviations found against your baseline.")
    }

    static func deferredView() -> NSView {
        badgeLine("bolt.slash.fill", DS.amber, "Scan deferred: system on battery power.")
    }

    private static func badgeLine(_ symbol: String, _ color: NSColor, _ text: String) -> NSView {
        let g = DS.symbol(symbol, 14, .medium, color)
        let row = NSStackView(views: [g, DS.text(text, 12, .regular, DS.label2, wrap: true), NSView()])
        row.orientation = .horizontal; row.spacing = 8; row.alignment = .centerY
        row.translatesAutoresizingMaskIntoConstraints = false
        row.heightAnchor.constraint(greaterThanOrEqualToConstant: 26).isActive = true
        return row
    }

    // ── the DeviationRow: the core visual atom (both sides always visible) ──
    static func deviationRow(_ f: [String: Any]) -> NSView {
        let render = f["render"] as? String ?? "neutral"
        let color = renderColor(render)
        let this = f["this"] as? [String: Any] ?? [:]
        let newQuote = this["raw_quote"] as? String ?? ""
        let baseline = f["baseline"] as? [String: Any]
        let detail = f["detail_label"] as? String ?? ""      // Slice 3c: server-authored plain-English reason
        let page = f["page"] as? Int                          // Slice 3c: citation provenance
        let reasoning = f["reasoning"] as? [String: Any]      // Slice 3c: canonical bounds for the disclosure

        let bg = color.withAlphaComponent(render == "strict" ? 0.12 : 0.06)
        let row = DS.rounded(bg: bg, radius: 8, border: color.withAlphaComponent(0.30), borderWidth: 0.5)
        row.translatesAutoresizingMaskIntoConstraints = false

        let head = NSStackView(views: [DS.symbol(renderGlyph(render), 12, .bold, color),
                                       DS.text(title(f["clause_type"] as? String ?? ""), 12.5, .semibold, DS.label),
                                       verdictBadge(f["verdict"] as? String, render, color), NSView()])
        head.orientation = .horizontal; head.spacing = 6; head.alignment = .centerY

        let col = NSStackView(views: [head]); col.orientation = .vertical; col.alignment = .leading; col.spacing = 4
        col.translatesAutoresizingMaskIntoConstraints = false
        func add(_ v: NSView) { col.addArrangedSubview(v); v.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true }

        // 1) the plain-English reason — always visible, every render class (Mirror, not Advisor)
        if !detail.isEmpty { add(DS.text(detail, 12, .regular, DS.label2, wrap: true)) }
        // 2) never just the verdict word: this contract's quote (with its page citation) AND the corpus standard
        if !newQuote.isEmpty { add(quoteLine("This contract", newQuote, page: page)) }
        if render != "qualitative", let b = baseline { add(quoteLine("Your standard", baselinePhrase(b), page: nil)) }
        // 3) the math, behind a click: the canonical bounds the deterministic comparator actually used
        if let r = reasoning, let rThis = r["this"] as? String {
            let detailView = reasoningView(rThis, r["standard"] as? String)
            detailView.isHidden = true
            // Construct the button FIRST, then set onPress — so `[weak toggle]` captures the LIVE button.
            // (The old form `toggle = DSButton(...){ [weak toggle] … }` captured `toggle` while it was still
            // nil — the closure was built as the initializer argument, before the assignment — so the guard
            // always returned and the click did nothing. DSButton.onPress is provided for exactly this.)
            let toggle = DSButton("Show the reasoning", symbol: "function", variant: .quiet, size: 11) {}
            toggle.onPress = { [weak detailView, weak toggle] in
                guard let detailView, let toggle else { return }
                detailView.isHidden.toggle()
                toggle.titleField?.stringValue = detailView.isHidden ? "Show the reasoning" : "Hide the reasoning"
                detailView.superview?.layoutSubtreeIfNeeded()   // reflow the stack immediately so it expands
            }
            col.addArrangedSubview(toggle)        // intrinsic width (a small leading button), not full-width
            add(detailView)
        }

        head.widthAnchor.constraint(equalTo: col.widthAnchor).isActive = true
        row.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 10),
            col.trailingAnchor.constraint(equalTo: row.trailingAnchor, constant: -10),
            col.topAnchor.constraint(equalTo: row.topAnchor, constant: 8),
            col.bottomAnchor.constraint(equalTo: row.bottomAnchor, constant: -8),
        ])
        return row
    }

    private static func quoteLine(_ label: String, _ quote: String, page: Int?) -> NSView {
        let tag = page.map { "\(label.uppercased())   ·   P. \($0)" } ?? label.uppercased()   // citation on the quote
        let col = NSStackView(views: [DS.text(tag, 10, .semibold, DS.label3),
                                      DS.text("“\(quote)”", 12, .regular, DS.label, wrap: true, selectable: true)])
        col.orientation = .vertical; col.alignment = .leading; col.spacing = 1
        col.translatesAutoresizingMaskIntoConstraints = false
        return col
    }

    /// The optional "show the reasoning" disclosure: the canonical bounds the deterministic comparator used.
    private static func reasoningView(_ thisBounds: String, _ standardBounds: String?) -> NSView {
        let box = DS.rounded(bg: DS.fill(0.05), radius: 6)
        box.translatesAutoresizingMaskIntoConstraints = false
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 2
        col.translatesAutoresizingMaskIntoConstraints = false
        col.addArrangedSubview(DS.text("This contract (normalized):  \(thisBounds)", 11, .regular, DS.label2, wrap: true, mono: true))
        if let s = standardBounds {
            col.addArrangedSubview(DS.text("Your standard (normalized):  \(s)", 11, .regular, DS.label2, wrap: true, mono: true))
        }
        box.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 8),
            col.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -8),
            col.topAnchor.constraint(equalTo: box.topAnchor, constant: 6),
            col.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -6),
        ])
        return box
    }

    private static func verdictBadge(_ verdict: String?, _ render: String, _ color: NSColor) -> NSView {
        DS.pill(verdictWord(verdict), symbol: renderGlyph(render), color: color)
    }

    // ── render-class -> styling (the only mapping this file owns) ──
    private static func renderColor(_ r: String) -> NSColor {
        switch r {
        case "strict":      return DS.amber                  // duration TIGHTER/LOOSER — warn, both quotes
        case "qualitative": return DS.blue                   // info badge — manual review
        default:            return DS.grey                   // neutral / unrankable — factual, no strictness color
        }
    }
    private static func renderGlyph(_ r: String) -> String {
        switch r {
        case "strict":      return "exclamationmark.triangle.fill"
        case "qualitative": return "info.circle.fill"
        default:            return "arrow.left.arrow.right"
        }
    }
    private static func verdictWord(_ v: String?) -> String {
        switch v {
        case "TIGHTER":                    return "Tighter"
        case "LOOSER":                     return "Looser"
        case "INCOMPARABLE_QUALITATIVE":   return "Qualitative"
        case "DIFFERS_IN_KIND_UNRANKABLE": return "Differs"
        case "EQUAL":                      return "Equal"
        default:                           return "Flagged"
        }
    }
    private static func title(_ clauseType: String) -> String {
        clauseType.split(separator: "_")
            .map { String($0.prefix(1)).uppercased() + String($0.dropFirst()) }
            .joined(separator: " ")
    }

    /// Presentation-only: the aggregate cohort median rendered for the human (NOT a verbatim corpus quote).
    private static func baselinePhrase(_ b: [String: Any]) -> String {
        let kind = b["kind"] as? String ?? ""
        let n = b["n"] as? Int ?? 0
        let median = (b["median"] as? Double) ?? Double((b["median"] as? Int) ?? 0)
        let noun: String
        switch kind {
        case "duration_calendar", "duration_business": noun = "\(fmtNum(median)) hours"
        case "percent":                                 noun = "\(fmtNum(median))%"
        default:                                        noun = fmtNum(median)   // money / count / other
        }
        return n > 0 ? "≈ \(noun)  (median of \(n))" : "≈ \(noun)"
    }
    private static let grouping: NumberFormatter = {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 2; return f
    }()
    private static func fmtNum(_ d: Double) -> String {
        grouping.string(from: NSNumber(value: d)) ?? String(d)
    }
}
