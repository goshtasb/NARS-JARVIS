// The `/` action-picker overlay (design handoff). A searchable list of catalog verbs, shown INLINE
// above the chat input (an NSView mounted in the chat, not a floating panel — panels were unreliable on
// macOS 26). The text field keeps focus and drives selection via the keyboard (↑/↓/⏎/Esc) routed from
// ChatView, so typing and navigating coexist. Each row: icon tile · name + description · Auto/Held pill.
import AppKit

final class ActionPicker: NSView {
    struct Verb { let name: String; let label: String; let desc: String; let symbol: String; let auto: Bool }

    private let list = NSStackView()
    private let scroll = NSScrollView()
    private var heightC: NSLayoutConstraint!
    private var all: [Verb] = []
    private(set) var filtered: [Verb] = []
    private var sel = 0
    var onChoose: ((Verb) -> Void)?

    init() {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        let bg = NSVisualEffectView()
        bg.material = .menu; bg.blendingMode = .behindWindow; bg.state = .active
        bg.wantsLayer = true; bg.layer?.cornerRadius = 12; bg.layer?.masksToBounds = true
        bg.layer?.borderWidth = 0.5; bg.layer?.borderColor = DS.separator.cgColor
        bg.translatesAutoresizingMaskIntoConstraints = false
        let header = DS.sectionHeader("Run a job — pick an action")
        scroll.drawsBackground = false; scroll.hasVerticalScroller = true
        scroll.translatesAutoresizingMaskIntoConstraints = false
        list.orientation = .vertical; list.alignment = .leading; list.spacing = 2
        list.translatesAutoresizingMaskIntoConstraints = false
        let flip = FlippedClip(); flip.translatesAutoresizingMaskIntoConstraints = false
        flip.addSubview(list); scroll.documentView = flip
        let footer = DS.text("↑ ↓ move    ↩ choose    esc dismiss", 10.5, .regular, DS.label3)
        let col = NSStackView(views: [header, scroll, footer])
        col.orientation = .vertical; col.alignment = .leading; col.spacing = 6
        col.edgeInsets = NSEdgeInsets(top: 10, left: 10, bottom: 8, right: 10)
        col.translatesAutoresizingMaskIntoConstraints = false
        bg.addSubview(col); addSubview(bg)
        // a soft drop shadow on the overlay
        wantsLayer = true; shadow = NSShadow()
        layer?.shadowColor = NSColor.black.withAlphaComponent(0.28).cgColor
        layer?.shadowOpacity = 1; layer?.shadowRadius = 14; layer?.shadowOffset = NSSize(width: 0, height: -6)
        heightC = scroll.heightAnchor.constraint(equalToConstant: 100)
        NSLayoutConstraint.activate([
            bg.leadingAnchor.constraint(equalTo: leadingAnchor), bg.trailingAnchor.constraint(equalTo: trailingAnchor),
            bg.topAnchor.constraint(equalTo: topAnchor), bg.bottomAnchor.constraint(equalTo: bottomAnchor),
            col.leadingAnchor.constraint(equalTo: bg.leadingAnchor), col.trailingAnchor.constraint(equalTo: bg.trailingAnchor),
            col.topAnchor.constraint(equalTo: bg.topAnchor), col.bottomAnchor.constraint(equalTo: bg.bottomAnchor),
            scroll.widthAnchor.constraint(equalTo: col.widthAnchor, constant: -20),
            flip.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            flip.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
            flip.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            list.leadingAnchor.constraint(equalTo: flip.leadingAnchor), list.trailingAnchor.constraint(equalTo: flip.trailingAnchor),
            list.topAnchor.constraint(equalTo: flip.topAnchor), list.bottomAnchor.constraint(equalTo: flip.bottomAnchor),
            list.widthAnchor.constraint(equalTo: scroll.widthAnchor),
            heightC,
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    func setVerbs(_ v: [Verb]) { all = v }

    func filter(_ query: String) {
        let q = query.lowercased()
        filtered = q.isEmpty ? all : all.filter {
            $0.name.lowercased().contains(q) || $0.label.lowercased().contains(q) || $0.desc.lowercased().contains(q)
        }
        if sel >= filtered.count { sel = max(0, filtered.count - 1) }
        heightC.constant = CGFloat(min(max(1, filtered.count), 6)) * 46
        render()
    }
    func move(_ d: Int) {
        guard !filtered.isEmpty else { return }
        sel = (sel + d + filtered.count) % filtered.count; render()
    }
    func chooseSelected() { if sel < filtered.count { onChoose?(filtered[sel]) } }
    func reset() { sel = 0 }

    private func render() {
        list.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if filtered.isEmpty {
            let none = DS.text("No actions match — try \"summarize\", \"network\", or \"open\".", 12, .regular, DS.label2)
            list.addArrangedSubview(none); none.widthAnchor.constraint(equalTo: list.widthAnchor, constant: -8).isActive = true
            return
        }
        for (i, v) in filtered.enumerated() {
            let r = row(v, focused: i == sel)
            list.addArrangedSubview(r)
            r.widthAnchor.constraint(equalTo: list.widthAnchor).isActive = true
        }
    }

    private func row(_ v: Verb, focused: Bool) -> NSView {
        let bg = DS.rounded(bg: focused ? DS.accent : .clear, radius: 7)
        let fg = focused ? DS.onAccent : DS.label
        let tile = DS.iconTile(v.symbol, tint: focused ? DS.onAccent : DS.accent, side: 28, pt: 13)
        let name = DS.text(v.label, 13, .medium, fg)
        let desc = DS.text(v.desc, 11.5, .regular, focused ? DS.onAccent.withAlphaComponent(0.85) : DS.label2)
        let textCol = NSStackView(views: [name, desc]); textCol.orientation = .vertical; textCol.alignment = .leading; textCol.spacing = 0
        let pill: NSView = focused ? DS.text(v.auto ? "Auto" : "Needs approval", 10.5, .semibold, DS.onAccent)
                                   : (v.auto ? DS.classPill(.auto) : DS.classPill(.held))
        let rowStack = NSStackView(views: [tile, textCol, NSView(), pill])
        rowStack.orientation = .horizontal; rowStack.spacing = 9; rowStack.alignment = .centerY
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        bg.addSubview(rowStack)
        NSLayoutConstraint.activate([
            bg.heightAnchor.constraint(equalToConstant: 44),
            rowStack.leadingAnchor.constraint(equalTo: bg.leadingAnchor, constant: 8),
            rowStack.trailingAnchor.constraint(equalTo: bg.trailingAnchor, constant: -8),
            rowStack.centerYAnchor.constraint(equalTo: bg.centerYAnchor),
        ])
        return bg
    }
}
