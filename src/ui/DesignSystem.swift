// The NARS-JARVIS visual system (design handoff) — native AppKit translation.
// Neutral graphite chrome · functional state colors · system accent for standard affordances only.
// Everything maps to SEMANTIC system colors so light/dark + the user's accent come for free; the few
// custom surfaces use dynamic (light/dark) NSColors matching the design tokens.
import AppKit

enum DS {
    // ── palette (semantic-first) ──
    static var label: NSColor { .labelColor }
    static var label2: NSColor { .secondaryLabelColor }
    static var label3: NSColor { .tertiaryLabelColor }
    static var label4: NSColor { .quaternaryLabelColor }
    static var separator: NSColor { .separatorColor }
    static var accent: NSColor { .controlAccentColor }
    static var green: NSColor { .systemGreen }
    static var amber: NSColor { .systemOrange }
    static var red: NSColor { .systemRed }
    static var blue: NSColor { .controlAccentColor }      // in-progress == accent (design intent)
    static var grey: NSColor { .systemGray }
    static var onAccent: NSColor { .white }

    // custom surfaces (token #F6F6F8 / #2A2A2C etc.) — dynamic so they adapt to appearance
    static let windowBG = NSColor.windowBackgroundColor
    static let contentBG = dynamic(light: .white, dark: NSColor(white: 0.118, alpha: 1))          // #1E1E1E
    static let card      = dynamic(light: NSColor(white: 0.965, alpha: 1),                          // #F6F6F8
                                   dark:  NSColor(white: 0.165, alpha: 1))                          // #2A2A2C
    static let card3     = dynamic(light: NSColor(white: 0.937, alpha: 1),                          // #EFEFF1
                                   dark:  NSColor(white: 0.145, alpha: 1))                          // #252527
    static let fieldBG   = dynamic(light: .white, dark: NSColor(white: 0.110, alpha: 1))            // #1C1C1E

    static func fill(_ a: CGFloat = 0.05) -> NSColor { dynamic(light: NSColor(white: 0, alpha: a),
                                                               dark: NSColor(white: 1, alpha: a + 0.02)) }
    static func tint(_ c: NSColor, _ a: CGFloat) -> NSColor { c.withAlphaComponent(a) }

    static func dynamic(light: NSColor, dark: NSColor) -> NSColor {
        NSColor(name: nil) { ap in ap.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light }
    }

    // ── type ──
    static func font(_ size: CGFloat, _ w: NSFont.Weight = .regular) -> NSFont { .systemFont(ofSize: size, weight: w) }
    static func mono(_ size: CGFloat = 12) -> NSFont { .monospacedSystemFont(ofSize: size, weight: .regular) }

    // ── SF Symbols ──
    static func symbol(_ name: String, _ size: CGFloat = 14, _ weight: NSFont.Weight = .regular,
                       _ color: NSColor? = nil) -> NSImageView {
        let iv = NSImageView()
        let cfg = NSImage.SymbolConfiguration(pointSize: size, weight: weight.symbolWeight())
        iv.image = NSImage(systemSymbolName: name, accessibilityDescription: name)?.withSymbolConfiguration(cfg)
        if let color { iv.contentTintColor = color }
        iv.translatesAutoresizingMaskIntoConstraints = false
        iv.setContentHuggingPriority(.required, for: .horizontal)
        iv.setContentHuggingPriority(.required, for: .vertical)
        return iv
    }

    /// A rounded, tinted icon tile (e.g. a verb tile or the mirror's eye tile).
    static func iconTile(_ symbolName: String, tint: NSColor, side: CGFloat = 30, pt: CGFloat = 14) -> NSView {
        let v = rounded(bg: tint.withAlphaComponent(0.16), radius: side * 0.3)
        let iv = symbol(symbolName, pt, .medium, tint)
        v.addSubview(iv)
        NSLayoutConstraint.activate([
            v.widthAnchor.constraint(equalToConstant: side), v.heightAnchor.constraint(equalToConstant: side),
            iv.centerXAnchor.constraint(equalTo: v.centerXAnchor), iv.centerYAnchor.constraint(equalTo: v.centerYAnchor),
        ])
        return v
    }

    // ── layer-backed rounded container ──
    static func rounded(bg: NSColor, radius: CGFloat, border: NSColor? = nil, borderWidth: CGFloat = 0.5) -> NSView {
        let v = LayerView()
        v.wantsLayer = true
        v.layer?.cornerRadius = radius
        v.bg = bg; v.border = border; v.borderW = borderWidth     // colored in updateLayer (appearance-correct)
        v.translatesAutoresizingMaskIntoConstraints = false
        return v
    }

    // ── labels ──
    static func text(_ s: String, _ size: CGFloat, _ w: NSFont.Weight = .regular, _ color: NSColor? = nil,
                     wrap: Bool = false, mono: Bool = false, selectable: Bool = false) -> NSTextField {
        let tf = wrap ? NSTextField(wrappingLabelWithString: s) : NSTextField(labelWithString: s)
        tf.font = mono ? DS.mono(size) : DS.font(size, w)
        tf.textColor = color ?? DS.label
        tf.isSelectable = selectable
        tf.translatesAutoresizingMaskIntoConstraints = false
        return tf
    }

    static func sectionHeader(_ s: String) -> NSTextField {
        let tf = text(s.uppercased(), 11, .semibold, DS.label3)
        return tf
    }

    // ── pills (Auto / Held action class) ──
    enum Klass { case auto, held }
    static func classPill(_ k: Klass) -> NSView {
        switch k {
        case .auto: return pill("Auto", symbol: "bolt.fill", color: DS.green)
        case .held: return pill("Needs approval", symbol: "pause.fill", color: DS.amber)
        }
    }

    static func pill(_ s: String, symbol sym: String?, color: NSColor) -> NSView {
        let v = rounded(bg: color.withAlphaComponent(0.16), radius: 9)
        let stack = NSStackView(); stack.orientation = .horizontal; stack.spacing = 3; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        if let sym { stack.addArrangedSubview(symbol(sym, 9, .bold, color)) }
        stack.addArrangedSubview(text(s, 10.5, .semibold, color))
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            v.heightAnchor.constraint(equalToConstant: 18),
            stack.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 7),
            stack.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -7),
            stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
        ])
        return v
    }

    // ── task state -> glyph + color + label ──
    static func stateColor(_ state: String) -> NSColor {
        switch state {
        case "done": return green
        case "running", "working": return blue
        case "failed": return red
        case "held", "scheduled", "pending": return state == "pending" ? grey : amber
        default: return grey
        }
    }
    static func stateGlyph(_ state: String) -> String {
        switch state {
        case "done": return "checkmark.circle.fill"
        case "running", "working": return "play.fill"
        case "failed": return "xmark.octagon.fill"
        case "held": return "pause.circle.fill"
        case "scheduled": return "calendar"
        default: return "hourglass"
        }
    }
    static func stateLabel(_ state: String) -> String {
        switch state {
        case "done": return "Done"
        case "running": return "Running"
        case "working": return "Working"
        case "failed": return "Failed"
        case "held": return "Needs approval"
        case "scheduled": return "Scheduled"
        default: return "Queued"
        }
    }
    static func stateBadge(_ state: String) -> NSView {
        pill(stateLabel(state), symbol: stateGlyph(state), color: stateColor(state))
    }
}

/// A plain layer-backed view that re-applies its colors when the appearance flips (cgColor doesn't auto-adapt).
/// A rounded, layer-backed view that colors itself in updateLayer() — AppKit calls that during the
/// display cycle with NSAppearance.current already set to the view's effectiveAppearance, so dynamic
/// (light/dark) colors always resolve correctly: on creation, on a runtime appearance toggle, and when
/// a detached subtree is re-shown. (cgColor set imperatively elsewhere uses the stale current appearance.)
final class LayerView: NSView {
    var bg: NSColor? { didSet { needsDisplay = true } }
    var border: NSColor? { didSet { needsDisplay = true } }
    var borderW: CGFloat = 0.5 { didSet { needsDisplay = true } }
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        layer?.backgroundColor = bg?.cgColor
        layer?.borderColor = border?.cgColor
        layer?.borderWidth = (border != nil) ? borderW : 0
    }
}

/// A closure-driven, layer-backed button with the design's variants (pill/rounded, custom fills).
final class DSButton: NSView {
    enum Variant { case primary, secondary, destructive, quiet, icon, pillAccent, stopPill }
    private let handler: () -> Void
    private let variant: Variant
    private let bgLayer = CALayer()
    private var hovering = false
    var titleField: NSTextField?
    private var symbolView: NSImageView?
    private var iconPt: CGFloat = 14

    init(_ title: String?, symbol: String? = nil, variant: Variant = .secondary,
         size: CGFloat = 12.5, square: CGFloat? = nil, radius: CGFloat? = nil,
         handler: @escaping () -> Void) {
        self.handler = handler; self.variant = variant
        super.init(frame: .zero)
        wantsLayer = true
        translatesAutoresizingMaskIntoConstraints = false
        layer?.cornerRadius = radius ?? ((variant == .icon) ? 6 : 7)
        let stack = NSStackView(); stack.orientation = .horizontal; stack.spacing = 6
        stack.alignment = .centerY; stack.translatesAutoresizingMaskIntoConstraints = false
        let fg = foreground()
        iconPt = size + 0.5
        if let symbol { let sv = DS.symbol(symbol, iconPt, .medium, fg); symbolView = sv; stack.addArrangedSubview(sv) }
        if let title {
            let t = DS.text(title, size, .medium, fg); titleField = t
            stack.addArrangedSubview(t)
        }
        addSubview(stack)
        if let square {                                   // a fixed square control (composer + / mic / send)
            NSLayoutConstraint.activate([
                widthAnchor.constraint(equalToConstant: square),
                heightAnchor.constraint(equalToConstant: square),
                stack.centerXAnchor.constraint(equalTo: centerXAnchor),
                stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            ])
        } else {
            let padX: CGFloat = (variant == .icon) ? 0 : 13
            NSLayoutConstraint.activate([
                heightAnchor.constraint(equalToConstant: variant == .icon ? 26 : 28),
                stack.centerYAnchor.constraint(equalTo: centerYAnchor),
                stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: padX),
                stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -padX),
            ])
            if variant == .icon { widthAnchor.constraint(equalToConstant: 30).isActive = true }
        }
        let area = NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeInActiveApp, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
    }
    required init?(coder: NSCoder) { fatalError() }

    private func foreground() -> NSColor {
        switch variant {
        case .primary, .pillAccent: return DS.onAccent
        case .destructive, .stopPill: return DS.red
        case .quiet: return DS.accent
        default: return DS.label
        }
    }
    private func baseBG() -> NSColor {
        switch variant {
        case .primary, .pillAccent: return DS.accent
        case .quiet, .icon: return .clear
        case .stopPill: return DS.red.withAlphaComponent(0.12)
        case .destructive: return DS.red.withAlphaComponent(0.0)
        default: return DS.contentBG
        }
    }
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        let base = baseBG()
        layer?.backgroundColor = (hovering ? hoverBG(base) : base).cgColor
        switch variant {
        case .secondary:
            layer?.borderWidth = 0.5; layer?.borderColor = DS.separator.withAlphaComponent(0.6).cgColor
        case .destructive, .stopPill:
            layer?.borderWidth = 0.5; layer?.borderColor = DS.red.withAlphaComponent(0.32).cgColor
        default:
            layer?.borderWidth = 0
        }
    }
    private func hoverBG(_ base: NSColor) -> NSColor {
        switch variant {
        case .primary, .pillAccent: return DS.accent.blended(withFraction: 0.08, of: .white) ?? base
        case .quiet, .icon: return DS.fill(0.08)
        case .destructive, .stopPill: return DS.red.withAlphaComponent(0.18)
        default: return DS.fill(0.06)
        }
    }
    func setSymbol(_ name: String) {
        let cfg = NSImage.SymbolConfiguration(pointSize: iconPt, weight: NSFont.Weight.medium.symbolWeight())
        symbolView?.image = NSImage(systemSymbolName: name, accessibilityDescription: name)?.withSymbolConfiguration(cfg)
    }
    override func hitTest(_ point: NSPoint) -> NSView? { bounds.contains(convert(point, from: superview)) ? self : nil }
    override func mouseEntered(with e: NSEvent) { hovering = true; needsDisplay = true }
    override func mouseExited(with e: NSEvent) { hovering = false; needsDisplay = true }
    override func mouseDown(with e: NSEvent) {}
    override func mouseUp(with e: NSEvent) {
        if bounds.contains(convert(e.locationInWindow, from: nil)) { handler() }
    }
}

private extension NSFont.Weight {
    func symbolWeight() -> NSFont.Weight { self }
}
