// The toolbar tab switcher (design handoff) — System-Settings-style: icon above a label, per segment,
// with a red active-task badge on a segment. A custom NSView (placed as one centered toolbar item) so
// clicks actually switch tabs and the sizing/alignment is exact — the NSToolbarItemGroup route gave us
// neither. Each segment: 64×40, 7pt radius; selected = fill background + full label color.
import AppKit

final class TabSwitcher: NSView {
    struct Item { let symbol: String; let label: String }
    var onSelect: ((Int) -> Void)?

    private var segments: [Segment] = []
    private(set) var selected = 0

    init(items: [Item]) {
        super.init(frame: NSRect(x: 0, y: 0, width: CGFloat(items.count) * 66, height: 40))
        translatesAutoresizingMaskIntoConstraints = false
        let stack = NSStackView(); stack.orientation = .horizontal; stack.spacing = 2; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        for (i, it) in items.enumerated() {
            let seg = Segment(item: it) { [weak self] in self?.select(i) }
            segments.append(seg); stack.addArrangedSubview(seg)
        }
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            heightAnchor.constraint(equalToConstant: 40),
        ])
        applySelection()
    }
    required init?(coder: NSCoder) { fatalError() }

    func select(_ i: Int, notify: Bool = true) {
        guard i >= 0, i < segments.count else { return }
        selected = i; applySelection()
        if notify { onSelect?(i) }
    }
    func setBadge(_ count: Int, at index: Int) {
        guard index < segments.count else { return }
        segments[index].setBadge(count)
    }
    private func applySelection() {
        for (i, s) in segments.enumerated() { s.setSelected(i == selected) }
    }

    // ── one segment ──
    final class Segment: NSView {
        private let onClick: () -> Void
        private let icon: NSImageView
        private let label: NSTextField
        private let badge = LayerView()
        private let badgeLabel = NSTextField(labelWithString: "")
        private var hovering = false
        private var selected = false

        init(item: Item, onClick: @escaping () -> Void) {
            self.onClick = onClick
            self.icon = DS.symbol(item.symbol, 17, .regular, DS.label2)
            self.label = DS.text(item.label, 10.5, .medium, DS.label2)
            super.init(frame: .zero)
            wantsLayer = true; layer?.cornerRadius = 7
            translatesAutoresizingMaskIntoConstraints = false
            let col = NSStackView(views: [icon, label]); col.orientation = .vertical
            col.alignment = .centerX; col.spacing = 1; col.translatesAutoresizingMaskIntoConstraints = false
            addSubview(col)
            // badge (hidden until count>0)
            badge.wantsLayer = true; badge.bg = DS.red; badge.layer?.backgroundColor = DS.red.cgColor
            badge.layer?.cornerRadius = 8; badge.isHidden = true
            badge.translatesAutoresizingMaskIntoConstraints = false
            badgeLabel.font = DS.font(10, .semibold); badgeLabel.textColor = .white
            badgeLabel.alignment = .center; badgeLabel.translatesAutoresizingMaskIntoConstraints = false
            badge.addSubview(badgeLabel); addSubview(badge)
            NSLayoutConstraint.activate([
                widthAnchor.constraint(greaterThanOrEqualToConstant: 64),
                heightAnchor.constraint(equalToConstant: 40),
                col.centerXAnchor.constraint(equalTo: centerXAnchor),
                col.centerYAnchor.constraint(equalTo: centerYAnchor),
                col.leadingAnchor.constraint(greaterThanOrEqualTo: leadingAnchor, constant: 12),
                col.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -12),
                badge.topAnchor.constraint(equalTo: topAnchor, constant: 1),
                badge.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -6),
                badge.heightAnchor.constraint(equalToConstant: 16),
                badge.widthAnchor.constraint(greaterThanOrEqualToConstant: 16),
                badgeLabel.centerXAnchor.constraint(equalTo: badge.centerXAnchor),
                badgeLabel.centerYAnchor.constraint(equalTo: badge.centerYAnchor),
                badgeLabel.leadingAnchor.constraint(equalTo: badge.leadingAnchor, constant: 4),
                badgeLabel.trailingAnchor.constraint(equalTo: badge.trailingAnchor, constant: -4),
            ])
            addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self))
        }
        required init?(coder: NSCoder) { fatalError() }

        func setSelected(_ on: Bool) {
            selected = on; refresh()
        }
        func setBadge(_ count: Int) {
            badge.isHidden = count <= 0
            badgeLabel.stringValue = "\(count)"
        }
        private func refresh() {
            let bg: NSColor = selected ? DS.fill(0.10) : (hovering ? DS.fill(0.05) : .clear)
            applyInCurrentAppearance { layer?.backgroundColor = bg.cgColor }
            let fg = selected ? DS.label : DS.label2
            icon.contentTintColor = fg; label.textColor = fg
        }
        override func hitTest(_ point: NSPoint) -> NSView? { bounds.contains(convert(point, from: superview)) ? self : nil }
        override func mouseEntered(with e: NSEvent) { hovering = true; refresh() }
        override func mouseExited(with e: NSEvent) { hovering = false; refresh() }
        override func mouseUp(with e: NSEvent) { if bounds.contains(convert(e.locationInWindow, from: nil)) { onClick() } }
        override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); refresh() }
    }
}
