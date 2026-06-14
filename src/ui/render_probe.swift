// Headless render harness (verification only): instantiate the panes, drive their offline preview
// seeds, and write each to a PNG so the layout is provable without a screen. Built as a separate
// `jarvis-render` binary. Usage: jarvis-render <out-dir>
import AppKit

@main
struct Render {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp"

        dump(makeChatDefault(), 960, 620, "\(outDir)/chat_default.png")     // On-device toggle in the composer
        dump(makeChatCloud(), 960, 620, "\(outDir)/chat_cloud.png")         // Cloud engaged + answer + footer
        dump(makeChatRecall(), 960, 640, "\(outDir)/chat_recall.png")       // grounded "Why" panel + Ask-Cloud
        dump(makeHabits(), 960, 700, "\(outDir)/identity_receipts.png")     // Privacy Receipts section
        dump(makeActivityLog(), 1000, 560, "\(outDir)/activity_log.png")    // Sprint 4: P0→P3 triage in the Log
        dump(makeActivityRisk(), 1000, 640, "\(outDir)/activity_risk.png")  // Slice 3b: the 4 progressive states
        print("RENDER-OK \(outDir)")
    }

    static func host(_ vc: NSViewController, _ w: CGFloat, _ h: CGFloat) -> NSView {
        let root = vc.view
        root.frame = NSRect(x: 0, y: 0, width: w, height: h)
        root.layoutSubtreeIfNeeded()
        return root
    }

    static func makeChatDefault() -> NSView {
        let vc = ChatViewController(); _ = vc.view
        return host(vc, 960, 620)
    }
    static func makeChatCloud() -> NSView {
        let vc = ChatViewController(); _ = vc.view
        vc.previewCloud()
        let v = host(vc, 960, 620); v.layoutSubtreeIfNeeded(); return v
    }
    static func makeChatRecall() -> NSView {
        let vc = ChatViewController(); _ = vc.view
        vc.previewRecall()
        let v = host(vc, 960, 640); v.layoutSubtreeIfNeeded(); return v
    }
    static func makeHabits() -> NSView {
        let vc = HabitsViewController(); _ = vc.view
        vc.previewSeed()
        let v = host(vc, 960, 700); v.layoutSubtreeIfNeeded(); return v
    }
    static func makeActivityLog() -> NSView {
        let vc = ActivityViewController(); _ = vc.view
        vc.previewSeed(2)   // the Log segment: held + failed (P0, accent) float above done (P3, compact)
        let v = host(vc, 1000, 560); v.layoutSubtreeIfNeeded(); return v
    }

    static func makeActivityRisk() -> NSView {
        let vc = ActivityViewController(); _ = vc.view
        vc.previewSeed(4)   // Risk: populated (strict/neutral/qualitative rows) · pending · empty · deferred
        let v = host(vc, 1000, 640); v.layoutSubtreeIfNeeded(); return v
    }

    static func dump(_ view: NSView, _ w: CGFloat, _ h: CGFloat, _ path: String) {
        view.frame = NSRect(x: 0, y: 0, width: w, height: h)
        view.layoutSubtreeIfNeeded()
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let data = rep.representation(using: .png, properties: [:]) else { return }
        try? data.write(to: URL(fileURLWithPath: path))
    }
}
