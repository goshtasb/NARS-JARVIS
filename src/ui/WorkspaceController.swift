// The single workspace window shell (design handoff). A real NSWindow with a FLAT custom 52pt top bar
// (NOT NSToolbar — macOS 26 draws rounded "glass" wells behind NSToolbar items, which the design does
// not have). The bar holds the title, a centered System-Settings-style tab switcher, a connection pill,
// a moon/sun appearance toggle, and a Stop button. Hosts the three panes and swaps them on tab change.
// Lifecycle: normal level, doesn't hide on deactivate, ⌘W hides (not quits), Dock icon while open.
import AppKit

final class WorkspaceController: NSObject, NSWindowDelegate {
    struct Pane { let vc: NSViewController; let symbol: String; let label: String }

    private let panes: [Pane]
    private(set) var window: NSWindow!
    private let container = NSView()
    private var current = -1
    private var built = false

    private var connDot: LayerView?
    private var connLabel: NSTextField?
    private var tabSwitcher: TabSwitcher?
    private var appearanceBtn: DSButton?
    private var connected = true

    var onStop: (() -> Void)?
    var onTabChanged: ((Int) -> Void)?

    init(panes: [Pane]) { self.panes = panes; super.init() }

    // ── build ──
    private func buildIfNeeded() {
        guard !built else { return }
        built = true
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 680),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                         backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.isMovableByWindowBackground = false
        w.isReleasedWhenClosed = false
        w.isRestorable = false          // don't let macOS restore a previously-resized (square) frame
        w.minSize = NSSize(width: 720, height: 520)
        w.delegate = self

        let root = NSView()
        let bar = buildBar()
        container.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(bar); root.addSubview(container)
        NSLayoutConstraint.activate([
            bar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            bar.topAnchor.constraint(equalTo: root.topAnchor),
            bar.heightAnchor.constraint(equalToConstant: 52),
            container.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            container.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            container.topAnchor.constraint(equalTo: bar.bottomAnchor),
            container.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        // contentView (NOT contentViewController) so the window keeps its 960×680 frame. Pane
        // viewDidAppear/Disappear are driven manually in selectTab().
        w.contentView = root
        w.center()
        window = w
        selectTab(0)
    }

    /// The flat 52pt bar: [JARVIS title] … (centered tabs) … [connection · moon/sun · Stop]. The traffic
    /// lights are the window's own buttons, overlaid top-left over the bar (title is inset to clear them).
    private func buildBar() -> NSView {
        let bar = NSVisualEffectView()
        bar.material = .headerView; bar.blendingMode = .behindWindow; bar.state = .followsWindowActiveState
        bar.translatesAutoresizingMaskIntoConstraints = false
        let hairline = DS.rounded(bg: DS.separator, radius: 0)
        bar.addSubview(hairline)

        let title = DS.text("JARVIS", 13, .semibold, DS.label2)
        title.translatesAutoresizingMaskIntoConstraints = false

        let switcher = TabSwitcher(items: panes.map { .init(symbol: $0.symbol, label: $0.label) })
        switcher.onSelect = { [weak self] i in self?.selectTab(i) }
        switcher.select(max(0, current), notify: false)
        tabSwitcher = switcher

        // connection pill
        let pill = DS.rounded(bg: DS.fill(0.05), radius: 13, border: DS.separator)
        let dot = DS.rounded(bg: connected ? DS.green : DS.amber, radius: 3.5)
        let lbl = DS.text(connected ? "Connected" : "Reconnecting…", 11.5, .medium, DS.label2)
        connDot = dot as? LayerView; connLabel = lbl
        let pillStack = NSStackView(views: [dot, lbl]); pillStack.spacing = 6; pillStack.alignment = .centerY
        pillStack.translatesAutoresizingMaskIntoConstraints = false
        pill.addSubview(pillStack)
        NSLayoutConstraint.activate([
            pill.heightAnchor.constraint(equalToConstant: 26),
            dot.widthAnchor.constraint(equalToConstant: 7), dot.heightAnchor.constraint(equalToConstant: 7),
            pillStack.leadingAnchor.constraint(equalTo: pill.leadingAnchor, constant: 9),
            pillStack.trailingAnchor.constraint(equalTo: pill.trailingAnchor, constant: -10),
            pillStack.centerYAnchor.constraint(equalTo: pill.centerYAnchor),
        ])

        let appr = DSButton(nil, symbol: appearanceSymbol(), variant: .icon, square: 26, radius: 13) { [weak self] in self?.appearancePressed() }
        appearanceBtn = appr
        let stop = DSButton("Stop", symbol: "stop.fill", variant: .stopPill, size: 11.5, radius: 13) { [weak self] in self?.stopPressed() }

        let right = NSStackView(views: [pill, appr, stop]); right.spacing = 8; right.alignment = .centerY
        right.translatesAutoresizingMaskIntoConstraints = false

        bar.addSubview(title); bar.addSubview(switcher); bar.addSubview(right)
        NSLayoutConstraint.activate([
            hairline.leadingAnchor.constraint(equalTo: bar.leadingAnchor),
            hairline.trailingAnchor.constraint(equalTo: bar.trailingAnchor),
            hairline.bottomAnchor.constraint(equalTo: bar.bottomAnchor),
            hairline.heightAnchor.constraint(equalToConstant: 0.5),
            title.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 84),  // clear the traffic lights
            title.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
            switcher.centerXAnchor.constraint(equalTo: bar.centerXAnchor),
            switcher.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
            right.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -12),
            right.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
        ])
        return bar
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
        forceRectangle()
        DispatchQueue.main.async { [weak self] in self?.forceRectangle() }   // re-assert after any late relayout/restoration
    }
    private func forceRectangle() {
        var f = window.frame; f.size = NSSize(width: 960, height: 680)
        window.setFrame(f, display: true, animate: false)
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
        connDot?.bg = up ? DS.green : DS.amber          // LayerView.updateLayer recolors
        connLabel?.stringValue = up ? "Connected" : "Reconnecting…"
    }
    func setActiveTaskCount(_ n: Int) { tabSwitcher?.setBadge(n, at: 1) }   // the red badge on the Canvas tab

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

    private func isDarkNow() -> Bool { NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua }
    private func appearanceSymbol() -> String { isDarkNow() ? "moon.fill" : "sun.max.fill" }   // shows current mode
    @objc private func appearancePressed() {
        NSApp.appearance = NSAppearance(named: isDarkNow() ? .aqua : .darkAqua)
        appearanceBtn?.setSymbol(appearanceSymbol())
    }
}
