// AX actuator (ADR-021). Runs an approved GUI verb against a real accessibility element. Crucially
// it RE-RESOLVES the element at actuation time (the async consent gate means the snapshot may be
// stale): it trusts the cached ref only if it still matches, else re-walks and matches by descriptor
// (AXIdentifier -> role+title -> indexPath). Drift or ambiguity -> abort, never guess. Requires the
// app to hold the Accessibility grant (AXIsProcessTrusted).
import ApplicationServices
import Foundation

enum AXActuator {
    /// Returns (ok, human-readable detail). Never throws; every failure path is reported truthfully.
    static func actuate(snapshot: AXSnapshot?, epoch: Int, id: String,
                        verb: String, args: [String: Any]) -> (Bool, String) {
        guard AXPermission.trusted() else {
            AXPermission.requestIfNeeded()
            return (false, "I need Accessibility access — grant JARVIS in System Settings → "
                         + "Privacy & Security → Accessibility, then try again.")
        }
        guard let snap = snapshot, snap.epoch == epoch else {
            return (false, "The screen changed since I read it — ask me again.")
        }
        guard let bound = snap.map[id] else {
            return (false, "I can't find that control anymore — ask me again.")
        }
        guard let target = resolve(bound: bound, pid: snap.pid) else {
            return (false, "That control moved or disappeared — ask me again.")
        }
        switch verb {
        case "ax_press":
            let err = AXUIElementPerformAction(target, kAXPressAction as CFString)
            return err == .success ? (true, "clicked \(label(bound))")
                                   : (false, "couldn't click \(label(bound)) (AX error \(err.rawValue))")
        case "ax_set_value":
            guard var v = doubleArg(args["value"]) else { return (false, "no value given") }
            if v > 1.0 { v = v / 100.0 }     // sliders are 0..1; accept "45" as 45%
            let num = v as CFNumber
            let err = AXUIElementSetAttributeValue(target, kAXValueAttribute as CFString, num)
            return err == .success ? (true, "set \(label(bound)) to \(Int(v * 100))%")
                                   : (false, "couldn't set \(label(bound)) (AX error \(err.rawValue))")
        case "ax_set_checked":
            // ADR-024 v1.0 polish: set-to-state, idempotent. Read the checkbox's current value and
            // press ONLY if it differs from the desired state — "turn on" means on, never a blind flip.
            guard let desired = doubleArg(args["value"]).map({ Int($0) }) else { return (false, "no target state") }
            var cv: CFTypeRef?
            let current: Int? = AXUIElementCopyAttributeValue(target, kAXValueAttribute as CFString, &cv) == .success
                ? (cv as? NSNumber)?.intValue : nil
            let on = desired == 1 ? "on" : "off"
            if current == desired { return (true, "\(label(bound)) is already \(on)") }
            let err = AXUIElementPerformAction(target, kAXPressAction as CFString)
            return err == .success ? (true, "turned \(label(bound)) \(on)")
                                   : (false, "couldn't set \(label(bound)) (AX error \(err.rawValue))")
        default:
            return (false, "unknown UI action: \(verb)")
        }
    }

    private static func label(_ b: AXBound) -> String {
        b.descriptor.title.map { "\"\($0)\"" } ?? b.descriptor.role
    }

    private static func doubleArg(_ a: Any?) -> Double? {
        if let d = a as? Double { return d }
        if let n = a as? NSNumber { return n.doubleValue }
        if let i = a as? Int { return Double(i) }
        if let s = a as? String { return Double(s) }
        return nil
    }

    // TOCTOU-safe re-resolution: cached element only if it still matches; else re-walk + match.
    private static func resolve(bound: AXBound, pid: pid_t) -> AXUIElement? {
        let d = bound.descriptor
        let cachedRole = AXSerializer.str(bound.element, kAXRoleAttribute)
        let cachedTitle = AXSerializer.str(bound.element, kAXTitleAttribute)
            ?? AXSerializer.str(bound.element, kAXDescriptionAttribute)
        if cachedRole == d.role, cachedTitle == d.title { return bound.element }   // still valid
        let live = AXSerializer.collect(pid: pid)
        if let ident = d.identifier {                                             // 1) AXIdentifier
            let hits = live.filter { $0.descriptor.identifier == ident }
            if hits.count == 1 { return hits[0].element }
        }
        let byTitle = live.filter { $0.descriptor.role == d.role && $0.descriptor.title == d.title }
        if byTitle.count == 1 { return byTitle[0].element }                       // 2) role + title
        let byPath = live.filter { $0.descriptor.role == d.role && $0.descriptor.indexPath == d.indexPath }
        if byPath.count == 1 { return byPath[0].element }                         // 3) index path
        return nil                                                                // ambiguous/gone -> abort
    }
}
