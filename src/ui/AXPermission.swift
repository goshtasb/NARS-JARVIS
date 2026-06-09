// AX permission gate (ADR-021). JARVIS.app is the only process that holds the Accessibility (TCC)
// grant — it's the "body" that reads and drives the GUI. Every actuation checks AXIsProcessTrusted()
// first and, if not trusted, prompts + deep-links System Settings rather than failing silently. The
// grant is keyed to this exact signed bundle, so an ad-hoc rebuild can invalidate it — we re-check
// every time and surface the need to re-grant.
import AppKit
import ApplicationServices

enum AXPermission {
    /// True if this app currently holds the Accessibility entitlement.
    static func trusted() -> Bool { AXIsProcessTrusted() }

    /// Check trust and, if missing, show the system Accessibility prompt (once per session-ish).
    @discardableResult
    static func requestIfNeeded() -> Bool {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue()
        return AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    /// Deep-link to System Settings → Privacy & Security → Accessibility so the user can grant/cycle it.
    static func openSettings() {
        if let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }
}
