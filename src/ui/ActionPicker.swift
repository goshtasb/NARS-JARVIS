// The `/` action-picker overlay (design handoff, marquee interaction). A floating, searchable list of
// catalog verbs anchored ABOVE the chat input. Built as a borderless child window (not the native
// field-editor completion, which tokenizes on `/` and was broken). The text field keeps focus and
// drives selection via the keyboard (↑/↓/⏎/Esc) routed from ChatView — so typing and navigating coexist.
import AppKit

final class ActionPicker {
    struct Verb { let name: String; let label: String; let desc: String; let symbol: String; let auto: Bool }

    private var panel: NSPanel?
    private let list = NSStackView()
    private var all: [Verb] = []
    private(set) var filtered: [Verb] = []
    private var sel = 0
    var onChoose: ((Verb) -> Void)?
    var isVisible: Bool { panel?.isVisible ?? false }

    func setVerbs(_ v: [Verb]) { all = v }

    private func buildPanel() {
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 300),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isFloatingPanel = true; p.level = .popUpMenu; p.hasShadow = true
        p.backgroundColor = .clear; p.isOpaque = false

        let bg = NSVisualEffectView()
        bg.material = .popover; bg.blendingMode = .behindWindow; bg.state = .active
        bg.wantsLayer = true; bg.layer?.cornerRadius = 12; bg.layer?.masksToBounds = true
        bg.layer?.borderWidth = 0.5; bg.layer?.borderColor = DS.separator.cgColor
        bg.translatesAutoresizingMaskIntoConstraints = false

        let header = DS.text("RUN A JOB — PICK AN ACTION", 10.5, .semibold, DS.label3)
        let scroll = NSScrollView(); scroll.drawsBackground = false; scroll.hasVerticalScroller = true
        scroll.translatesAutoresizingMaskIntoConstraints = false
        list.orientation = .vertical; list.alignment = .leading; list.spacing = 2
        list.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = list
        let footer = DS.text("↑ ↓ move    ↩ choose    esc dismiss", 10.5, .regular, DS.label3)

        let col = NSStackView(views: [header, scroll, footer])
        col.orientation = .vertical; col.alignment = .leading; col.spacing = 6
        col.edgeInsets = NSEdgeInsets(top: 10, left: 10, bottom: 8, right: 10)
        col.translatesAutoresizingMaskIntoConstraints = false
        bg.addSubview(col)
        p.contentView?.addSubview(bg)
        NSLayoutConstraint.activate([
            bg.leadingAnchor.constraint(equalTo: p.contentView!.leadingAnchor),
            bg.trailingAnchor.constraint(equalTo: p.contentView!.trailingAnchor),
            bg.topAnchor.constraint(equalTo: p.contentView!.topAnchor),
            bg.bottomAnchor.constraint(equalTo: p.contentView!.bottomAnchor),
            col.leadingAnchor.constraint(equalTo: bg.leadingAnchor),
            col.trailingAnchor.constraint(equalTo: bg.trailingAnchor),
            col.topAnchor.constraint(equalTo: bg.topAnchor),
            col.bottomAnchor.constraint(equalTo: bg.bottomAnchor),
            scroll.widthAnchor.constraint(equalTo: col.widthAnchor, constant: -20),
            list.widthAnchor.constraint(equalTo: scroll.widthAnchor),
        ])
        panel = p
    }

    func update(anchor: NSView, query: String) {
        if panel == nil { buildPanel() }
        guard let panel, let parent = anchor.window else { return }
        let q = query.lowercased()
        filtered = q.isEmpty ? all : all.filter {
            $0.name.lowercased().contains(q) || $0.label.lowercased().contains(q) || $0.desc.lowercased().contains(q)
        }
        if sel >= filtered.count { sel = max(0, filtered.count - 1) }
        render()
        // size + position the panel ABOVE the input field
        let rows = min(filtered.count, 6)
        let h = CGFloat(56 + max(1, rows) * 46)
        let w = max(380, anchor.bounds.width)
        let fieldRectInWin = anchor.convert(anchor.bounds, to: nil)
        let fieldOnScreen = parent.convertToScreen(fieldRectInWin)
        let origin = NSPoint(x: fieldOnScreen.minX, y: fieldOnScreen.maxY + 6)
        panel.setFrame(NSRect(x: origin.x, y: origin.y, width: w, height: h), display: true)
        if !panel.isVisible { parent.addChildWindow(panel, ordered: .above) }
        panel.orderFront(nil)
    }

    func move(_ d: Int) {
        guard !filtered.isEmpty else { return }
        sel = (sel + d + filtered.count) % filtered.count
        render()
    }
    func chooseSelected() {
        guard sel < filtered.count else { return }
        let v = filtered[sel]; hide(); onChoose?(v)
    }
    func hide() {
        guard let panel else { return }
        panel.parent?.removeChildWindow(panel); panel.orderOut(nil); sel = 0
    }

    private func render() {
        list.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if filtered.isEmpty {
            let none = DS.text("No actions match — try \"summarize\", \"network\", or \"open\".", 12, .regular, DS.label2, wrap: true)
            none.translatesAutoresizingMaskIntoConstraints = false
            list.addArrangedSubview(none)
            return
        }
        for (i, v) in filtered.enumerated() { list.addArrangedSubview(row(v, focused: i == sel)) }
    }

    private func row(_ v: Verb, focused: Bool) -> NSView {
        let bg = DS.rounded(bg: focused ? DS.accent : .clear, radius: 7)
        let fg = focused ? DS.onAccent : DS.label
        let tile = DS.iconTile(v.symbol, tint: focused ? DS.onAccent : DS.accent, side: 30, pt: 14)
        let name = DS.text(v.label, 13, .medium, fg)
        let desc = DS.text(v.desc, 11.5, .regular, focused ? DS.onAccent.withAlphaComponent(0.85) : DS.label2)
        let textCol = NSStackView(views: [name, desc]); textCol.orientation = .vertical
        textCol.alignment = .leading; textCol.spacing = 1
        let pill = focused ? DS.text(v.auto ? "Auto" : "Needs approval", 10.5, .semibold, DS.onAccent)
                           : (v.auto ? DS.classPill(.auto) : DS.classPill(.held))
        let rowStack = NSStackView(views: [tile, textCol, NSView(), pill])
        rowStack.orientation = .horizontal; rowStack.spacing = 9; rowStack.alignment = .centerY
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        bg.addSubview(rowStack)
        NSLayoutConstraint.activate([
            bg.heightAnchor.constraint(equalToConstant: 44),
            bg.widthAnchor.constraint(equalTo: list.widthAnchor),
            rowStack.leadingAnchor.constraint(equalTo: bg.leadingAnchor, constant: 8),
            rowStack.trailingAnchor.constraint(equalTo: bg.trailingAnchor, constant: -8),
            rowStack.centerYAnchor.constraint(equalTo: bg.centerYAnchor),
        ])
        return bg
    }
}
