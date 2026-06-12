// The single workspace window shell (design handoff) — a real NSWindow with a unified toolbar:
// System-Settings-style centered tab group (icon + label), a connection pill, an appearance toggle, and
// a Stop button. Hosts the three pane view controllers and swaps them on tab change. Replaces the old
// NSTabViewController. Lifecycle: normal level, doesn't hide on deactivate, ⌘W hides (not quits), Dock
// icon appears while open (.regular) and hides when closed (.accessory) for the "real window" feel.
import AppKit

final class WorkspaceController: NSObject, NSToolbarDelegate, NSWindowDelegate {
    struct Pane { let vc: NSViewController; let symbol: String; let label: String }

    private let panes: [Pane]
    private(set) var window: NSWindow!
    private let container = NSView()
    private var current = -1
    private var built = false

    // toolbar-right live views
    private var connDot: LayerView?
    private var connLabel: NSTextField?
    private var tabSwitcher: TabSwitcher?
    private var connected = true
    private var badgeCount = 0

    var onStop: (() -> Void)?
    var onTabChanged: ((Int) -> Void)?

    private static let tabsID = NSToolbarItem.Identifier("tabs")
    private static let connID = NSToolbarItem.Identifier("connection")
    private static let apprID = NSToolbarItem.Identifier("appearance")
    private static let stopID = NSToolbarItem.Identifier("stop")

    init(panes: [Pane]) { self.panes = panes; super.init() }

    // ── build ──
    private func buildIfNeeded() {
        guard !built else { return }
        built = true
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 680),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],   // NO fullSizeContentView
                         backing: .buffered, defer: false)
        w.title = "JARVIS"
        w.titleVisibility = .hidden        // the centered tab group is the focus; avoids title/content overlap
        w.isReleasedWhenClosed = false
        w.minSize = NSSize(width: 720, height: 520)
        w.delegate = self

        container.translatesAutoresizingMaskIntoConstraints = false
        let root = NSView()
        root.addSubview(container)
        NSLayoutConstraint.activate([
            container.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            container.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            container.topAnchor.constraint(equalTo: root.topAnchor),
            container.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        // contentView (NOT contentViewController) so the window keeps its 960×680 frame — a
        // contentViewController auto-shrinks the window to its view's fitting size (the "square" bug).
        // Pane viewDidAppear/Disappear are driven manually in selectTab().
        w.contentView = root

        let tb = NSToolbar(identifier: "jarvis.workspace")
        tb.delegate = self
        tb.displayMode = .iconOnly        // custom view items carry their own labels; no redundant toolbar labels
        tb.allowsUserCustomization = false
        if #available(macOS 13.0, *) { tb.centeredItemIdentifiers = [Self.tabsID] }
        w.toolbar = tb
        if #available(macOS 11.0, *) { w.toolbarStyle = .unified }
        w.center()
        window = w
        selectTab(0)
    }

    // ── show / hide / toggle (Dock policy flips for the real-window feel) ──
    func toggle() {
        buildIfNeeded()
        if window.isVisible && window.isKeyWindow { hide() } else { show() }
    }
    func show() {
        buildIfNeeded()
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    private func hide() {
        window.orderOut(nil)
        NSApp.setActivationPolicy(.accessory)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { hide(); return false }   // ⌘W / red button hides

    func selectTab(_ i: Int) {
        guard i >= 0, i < panes.count, i != current else { return }
        if current >= 0 {
            let old = panes[current].vc
            old.view.removeFromSuperview(); old.viewDidDisappear()        // manual lifecycle (no parent VC)
        }
        current = i
        let vc = panes[i].vc
        let v = vc.view                                                  // loads the view
        v.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(v)
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            v.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            v.topAnchor.constraint(equalTo: container.topAnchor),
            v.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        vc.viewDidAppear()                                               // fetch verbs / poll / refresh
        tabSwitcher?.select(i, notify: false)
        onTabChanged?(i)
    }

    // ── connection pill + Canvas badge ──
    func setConnected(_ up: Bool) {
        connected = up
        connDot?.bg = up ? DS.green : DS.amber
        connDot?.layer?.backgroundColor = (up ? DS.green : DS.amber).cgColor
        connLabel?.stringValue = up ? "Connected" : "Reconnecting…"
    }
    func setActiveTaskCount(_ n: Int) {
        badgeCount = n
        tabSwitcher?.setBadge(n, at: 1)         // the red badge on the Canvas tab
    }

    // ── Stop everything (confirmation sheet) ──
    @objc private func stopPressed() {
        let a = NSAlert()
        a.messageText = "Stop everything?"
        a.informativeText = "This cancels all running, working, and queued jobs and disconnects the engine."
        a.alertStyle = .critical
        a.addButton(withTitle: "Stop everything")
        a.addButton(withTitle: "Cancel")
        a.buttons.first?.hasDestructiveAction = true
        a.beginSheetModal(for: window) { [weak self] resp in
            if resp == .alertFirstButtonReturn { self?.onStop?() }
        }
    }

    @objc private func appearancePressed() {
        let dark = (NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua)
        NSApp.appearance = NSAppearance(named: dark ? .aqua : .darkAqua)
    }

    // ── NSToolbarDelegate ──
    func toolbarDefaultItemIdentifiers(_ t: NSToolbar) -> [NSToolbarItem.Identifier] {
        [Self.tabsID, .flexibleSpace, Self.connID, Self.apprID, Self.stopID]
    }
    func toolbarAllowedItemIdentifiers(_ t: NSToolbar) -> [NSToolbarItem.Identifier] {
        toolbarDefaultItemIdentifiers(t)
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        switch id {
        case Self.tabsID:
            let switcher = TabSwitcher(items: panes.map { .init(symbol: $0.symbol, label: $0.label) })
            switcher.onSelect = { [weak self] i in self?.selectTab(i) }
            switcher.select(max(0, current), notify: false)
            tabSwitcher = switcher
            let item = NSToolbarItem(itemIdentifier: id)
            item.view = switcher
            return item
        case Self.connID:
            let item = NSToolbarItem(itemIdentifier: id)
            let pill = DS.rounded(bg: DS.fill(0.05), radius: 13, border: DS.separator)
            let dot = DS.rounded(bg: connected ? DS.green : DS.amber, radius: 3.5)
            let lbl = DS.text(connected ? "Connected" : "Reconnecting…", 11.5, .medium, DS.label2)
            connDot = dot as? LayerView; connLabel = lbl
            let stack = NSStackView(views: [dot, lbl]); stack.spacing = 6; stack.alignment = .centerY
            stack.translatesAutoresizingMaskIntoConstraints = false
            pill.addSubview(stack)
            NSLayoutConstraint.activate([
                pill.heightAnchor.constraint(equalToConstant: 26),
                dot.widthAnchor.constraint(equalToConstant: 7), dot.heightAnchor.constraint(equalToConstant: 7),
                stack.leadingAnchor.constraint(equalTo: pill.leadingAnchor, constant: 9),
                stack.trailingAnchor.constraint(equalTo: pill.trailingAnchor, constant: -10),
                stack.centerYAnchor.constraint(equalTo: pill.centerYAnchor),
            ])
            item.view = pill
            return item
        case Self.apprID:
            let item = NSToolbarItem(itemIdentifier: id)
            item.view = DSButton(nil, symbol: "circle.lefthalf.filled", variant: .icon) { [weak self] in self?.appearancePressed() }
            item.label = "Appearance"
            return item
        case Self.stopID:
            let item = NSToolbarItem(itemIdentifier: id)
            item.view = DSButton("Stop", symbol: "stop.fill", variant: .stopPill, size: 11.5, radius: 13) { [weak self] in self?.stopPressed() }
            item.label = "Stop"
            return item
        default:
            return nil
        }
    }
}
