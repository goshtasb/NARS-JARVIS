// Global push-to-talk hotkey via Carbon RegisterEventHotKey. Deliberately NOT NSEvent global
// monitoring: a *registered* hotkey asks the OS to deliver one chord (⌥Space) and is not keystroke
// monitoring, so it does NOT trigger the Accessibility / Input-Monitoring TCC dialog (ADR-005).
// Carbon delivers both pressed and released, so true hold-to-talk works. Singleton so the C callback
// (which can't capture Swift context) can reach the handlers.
import Carbon
import Foundation

final class HotKey {
    static let shared = HotKey()
    private var ref: EventHotKeyRef?
    var onPressed: (() -> Void)?
    var onReleased: (() -> Void)?

    /// Register ⌥Space (default). Returns false if the OS refused the registration.
    @discardableResult
    func register(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(optionKey)) -> Bool {
        var spec = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]
        InstallEventHandler(GetEventDispatcherTarget(), { (_, event, _) -> OSStatus in
            guard let event = event else { return noErr }
            if GetEventKind(event) == UInt32(kEventHotKeyPressed) {
                HotKey.shared.onPressed?()
            } else {
                HotKey.shared.onReleased?()
            }
            return noErr
        }, 2, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x4a565253), id: 1)   // 'JVRS'
        let status = RegisterEventHotKey(keyCode, modifiers, id, GetEventDispatcherTarget(), 0, &ref)
        return status == noErr
    }
}
