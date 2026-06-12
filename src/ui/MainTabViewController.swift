// The single workspace window's tab host (ADR-055): Chat · Canvas · Cognitive Identity, each an
// EXISTING NSViewController dropped in unchanged. Replaces the three scattered menu-bar popovers/window
// with one cohesive window. Resizes the window to the selected pane's natural size (System-Settings
// style) so a smaller pane isn't stranded in a corner — no pane internals are touched.
import AppKit

final class MainTabViewController: NSTabViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        tabStyle = .toolbar          // tabs in the window's toolbar (native macOS settings-style)
        transitionOptions = []       // instant switch, no cross-fade
    }

    override func tabView(_ tabView: NSTabView, didSelect item: NSTabViewItem?) {
        super.tabView(tabView, didSelect: item)
        guard let vc = item?.viewController, let window = view.window else { return }
        let size = vc.preferredContentSize == .zero ? vc.view.frame.size : vc.preferredContentSize
        if size.width > 0 && size.height > 0 { window.setContentSize(size) }   // fit the window to the pane
    }
}
