// AX tree serializer (ADR-021). Walks the focused window of a target app, aggressively prunes to
// actionable controls, assigns each a stable string id, and emits a compact text DOM for the LLM
// prompt. The id->element map (with a re-resolvable descriptor) stays IN THIS PROCESS — only strings
// (dom + ids) cross the socket. AXUIElementRef is ephemeral, so we also keep a descriptor used to
// re-resolve at actuation time (TOCTOU-safe; see AXActuator).
import ApplicationServices
import Foundation

// What identifies an element well enough to find it again after the tree shifts.
struct AXDescriptor {
    let role: String
    let title: String?       // AXTitle or AXDescription
    let identifier: String?  // AXIdentifier — developer-assigned, the gold key when present
    let indexPath: [Int]     // child-index path from the window root (last-resort matcher)
}

struct AXBound { let element: AXUIElement; let descriptor: AXDescriptor }

struct AXSnapshot {
    let epoch: Int
    let pid: pid_t
    let dom: String                 // newline-joined, e.g.  [sld_1] AXSlider "Brightness" = 0.6
    let ids: [String]
    let map: [String: AXBound]      // id -> live element + descriptor (in-process only)
}

enum AXSerializer {
    // Only these roles are worth showing the model; everything else is structural noise we discard.
    static let actionableRoles: Set<String> = [
        "AXButton", "AXSlider", "AXCheckBox", "AXTextField", "AXTextArea",
        "AXPopUpButton", "AXRadioButton", "AXMenuItem", "AXMenuButton",
        "AXDisclosureTriangle", "AXIncrementor",
    ]
    static let maxDepth = 14
    static let maxNodes = 50

    // ── attribute readers ──
    static func str(_ el: AXUIElement, _ attr: String) -> String? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, attr as CFString, &v) == .success else { return nil }
        if let s = v as? String { return s }
        return nil
    }
    static func valueString(_ el: AXUIElement) -> String? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXValueAttribute as CFString, &v) == .success else { return nil }
        if let n = v as? NSNumber { return n.stringValue }
        if let s = v as? String { return s }
        return nil
    }
    static func children(_ el: AXUIElement) -> [AXUIElement] {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &v) == .success,
              let arr = v as? [AXUIElement] else { return [] }
        return arr
    }

    // ── ADR-024 label enrichment ── recover a control's label when its own AXTitle is empty. Priority
    // cascade: explicit OS-level link first, structural/spatial sibling last (never spatial-first).
    static func enrichedLabel(_ el: AXUIElement) -> String? {
        if let t = nonEmpty(str(el, kAXTitleAttribute)) { return t }           // own title (the normal case)
        if let linked = elementAttr(el, kAXTitleUIElementAttribute),           // explicit link (SwiftUI/Catalyst)
           let t = staticText(linked) { return t }
        if let d = nonEmpty(str(el, kAXDescriptionAttribute)) { return d }     // own description / help
        if let h = nonEmpty(str(el, kAXHelpAttribute)) { return h }
        return nearbyStaticText(el)                                            // structural sibling (spatial-nearest)
    }

    private static func nonEmpty(_ s: String?) -> String? {
        let t = s?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (t?.isEmpty == false) ? t : nil
    }

    /// Read an attribute that yields a linked AXUIElement (AXTitleUIElement, AXParent).
    static func elementAttr(_ el: AXUIElement, _ attr: String) -> AXUIElement? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, attr as CFString, &v) == .success, let v = v,
              CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return (v as! AXUIElement)
    }

    /// The visible string of an AXStaticText-like node (its value or title).
    private static func staticText(_ el: AXUIElement) -> String? {
        return nonEmpty(str(el, kAXValueAttribute)) ?? nonEmpty(str(el, kAXTitleAttribute))
    }

    static func frame(_ el: AXUIElement) -> CGRect? {
        var pv: CFTypeRef?
        var sv: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXPositionAttribute as CFString, &pv) == .success,
              AXUIElementCopyAttributeValue(el, kAXSizeAttribute as CFString, &sv) == .success else { return nil }
        var pt = CGPoint.zero
        var sz = CGSize.zero
        AXValueGetValue(pv as! AXValue, .cgPoint, &pt)
        AXValueGetValue(sv as! AXValue, .cgSize, &sz)
        return CGRect(origin: pt, size: sz)
    }

    /// Nearest AXStaticText among the control's siblings (structural grouping), tie-broken by spatial
    /// proximity. This is the last-resort tier — used only when no explicit link / own label exists.
    private static func nearbyStaticText(_ el: AXUIElement) -> String? {
        guard let parent = elementAttr(el, kAXParentAttribute) else { return nil }
        let here = frame(el)
        var best: (text: String, dist: CGFloat)?
        for sib in children(parent) where !CFEqual(sib, el) {
            guard let txt = staticTextIn(sib, depth: 2) else { continue }
            let d: CGFloat = (here != nil && frame(sib) != nil)
                ? hypot(here!.midX - frame(sib)!.midX, here!.midY - frame(sib)!.midY) : .greatestFiniteMagnitude
            if best == nil || d < best!.dist { best = (txt, d) }
        }
        return best?.text
    }

    /// First non-empty AXStaticText at `el` or within a shallow descent (a sibling may wrap its label).
    private static func staticTextIn(_ el: AXUIElement, depth: Int) -> String? {
        if (str(el, kAXRoleAttribute) ?? "") == "AXStaticText", let t = staticText(el) { return t }
        if depth <= 0 { return nil }
        for c in children(el) { if let t = staticTextIn(c, depth: depth - 1) { return t } }
        return nil
    }

    static func focusedWindow(_ app: AXUIElement) -> AXUIElement? {
        for attr in [kAXFocusedWindowAttribute, kAXMainWindowAttribute] {
            var v: CFTypeRef?
            if AXUIElementCopyAttributeValue(app, attr as CFString, &v) == .success, let w = v {
                return (w as! AXUIElement)
            }
        }
        var v: CFTypeRef?
        if AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &v) == .success,
           let arr = v as? [AXUIElement], let first = arr.first { return first }
        return nil
    }

    /// Collect actionable elements (with descriptors) from a target app's focused window. Shared by
    /// serialize() and AXActuator's re-resolution so there is ONE walk implementation.
    static func collect(pid: pid_t) -> [AXBound] {
        let app = AXUIElementCreateApplication(pid)
        guard let window = focusedWindow(app) else { return [] }
        var out: [AXBound] = []
        walk(window, path: [], depth: 0, out: &out)
        return out
    }

    private static func walk(_ el: AXUIElement, path: [Int], depth: Int, out: inout [AXBound]) {
        if depth > maxDepth || out.count >= maxNodes { return }
        let role = str(el, kAXRoleAttribute) ?? ""
        if actionableRoles.contains(role) {
            // ADR-024: the label cascade (incl. sibling/frame traversal) runs ONLY for actionable
            // controls — never the many container nodes — so serialization stays fast.
            let title = enrichedLabel(el)
            if title != nil || valueString(el) != nil {
                out.append(AXBound(element: el, descriptor: AXDescriptor(
                    role: role, title: title, identifier: str(el, kAXIdentifierAttribute), indexPath: path)))
                if out.count >= maxNodes { return }
            }
        }
        for (i, child) in children(el).enumerated() {
            walk(child, path: path + [i], depth: depth + 1, out: &out)
        }
    }

    /// Serialize a target app into a snapshot: assign ids, build the text DOM, keep the in-process map.
    static func serialize(pid: pid_t, epoch: Int) -> AXSnapshot {
        let bound = collect(pid: pid)
        var map: [String: AXBound] = [:]
        var lines: [String] = []
        var ids: [String] = []
        var counters: [String: Int] = [:]
        for b in bound {
            let short = b.descriptor.role.replacingOccurrences(of: "AX", with: "").lowercased()
            counters[short, default: 0] += 1
            let id = "\(short)_\(counters[short]!)"
            map[id] = b
            ids.append(id)
            let title = b.descriptor.title.map { " \"\($0)\"" } ?? ""
            let value = valueString(b.element).map { " = \($0)" } ?? ""
            lines.append("[\(id)] \(b.descriptor.role)\(title)\(value)")
        }
        return AXSnapshot(epoch: epoch, pid: pid, dom: lines.joined(separator: "\n"), ids: ids, map: map)
    }
}
