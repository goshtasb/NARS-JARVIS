// Dual-Brain client state (ADR-056) — the Swift side of General Mode.
//
// Three concerns, kept out of ChatView so the composer stays a view:
//  1. CloudKeychain — the API key lives in the macOS Keychain (Security framework), NEVER in the
//     environment (safespawn.scrub_environ would delete it) and NEVER on the daemon. The client reads it
//     here and passes it per-request over the socket; the daemon is credential-stateless.
//  2. CloudPrefs — the one-time-disclosure flag + the chosen provider (UserDefaults; non-secret).
//  3. CloudUI — the one-time disclosure sheet (negative-space framing) + the key-entry sheet.
import AppKit
import Security

enum CloudProvider: String {
    case openai, anthropic
    var display: String { self == .openai ? "OpenAI" : "Claude" }
    var keyHint: String { self == .openai ? "sk-…" : "sk-ant-…" }
}

enum CloudKeychain {
    private static let service = "com.nars-jarvis.cloud"

    static func store(_ key: String, provider: CloudProvider) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: provider.rawValue]
        SecItemDelete(base as CFDictionary)
        guard !key.isEmpty, let data = key.data(using: .utf8) else { return }
        var add = base; add[kSecValueData as String] = data
        SecItemAdd(add as CFDictionary, nil)
    }

    static func load(_ provider: CloudProvider) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: provider.rawValue,
                                kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let d = out as? Data, let s = String(data: d, encoding: .utf8) else { return nil }
        return s
    }

    static func clear(_ provider: CloudProvider) {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrService as String: service,
                       kSecAttrAccount as String: provider.rawValue] as CFDictionary)
    }
}

enum CloudPrefs {
    static var disclosureShown: Bool {
        get { UserDefaults.standard.bool(forKey: "cloud.disclosureShown") }
        set { UserDefaults.standard.set(newValue, forKey: "cloud.disclosureShown") }
    }
    static var provider: CloudProvider {
        get { CloudProvider(rawValue: UserDefaults.standard.string(forKey: "cloud.provider") ?? "openai") ?? .openai }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: "cloud.provider") }
    }
}

enum CloudUI {
    /// The ONE-TIME disclosure, framed by negative space — what NEVER leaves is heavier than what's sent.
    /// `proceed(true)` means the user accepts; show this only the first time Cloud is engaged.
    static func presentDisclosure(on window: NSWindow?, provider: CloudProvider, proceed: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = "Ask the Cloud?"
        a.informativeText = """
        This question will leave your Mac and go to \(provider.display).

        What's sent:  your message, and JARVIS's working notes for this task.

        What never leaves:  your memory, your habits, your files, your identity — those stay on this \
        Mac, in every mode. The default is always On-device.
        """
        a.addButton(withTitle: "Ask the Cloud")
        a.addButton(withTitle: "Stay Private")
        let run: (NSApplication.ModalResponse) -> Void = { resp in proceed(resp == .alertFirstButtonReturn) }
        if let window { a.beginSheetModal(for: window, completionHandler: run) } else { run(a.runModal()) }
    }

    /// Key-entry sheet -> Keychain. `done(true)` if a key was saved.
    static func presentKeyEntry(on window: NSWindow?, provider: CloudProvider, done: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = "Connect \(provider.display)"
        a.informativeText = "Paste your \(provider.display) API key. It's stored in your macOS Keychain — never on the JARVIS engine, never in plain text."
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.placeholderString = provider.keyHint
        field.stringValue = CloudKeychain.load(provider) ?? ""
        a.accessoryView = field
        a.addButton(withTitle: "Save")
        a.addButton(withTitle: "Cancel")
        let finish: (NSApplication.ModalResponse) -> Void = { resp in
            guard resp == .alertFirstButtonReturn else { done(false); return }
            let key = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            CloudKeychain.store(key, provider: provider)
            done(!key.isEmpty)
        }
        if let window {
            window.makeFirstResponder(field)
            a.beginSheetModal(for: window, completionHandler: finish)
        } else { finish(a.runModal()) }
    }
}
