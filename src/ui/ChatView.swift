// Chat — the Universal Composer (design handoff). A conversation that doubles as a natural-language +
// `/`-command composer. Full-window layout: a scrollable transcript of styled bubbles fills the top; a
// pinned composer bar (attach · `/` · field · mic · send) sits at the bottom. Typing `/` opens the
// ActionPicker overlay (searchable verb list, keyboard-driven). Strictly a view over JarvisClient.
import AppKit

final class ChatViewController: NSViewController, NSTextFieldDelegate {
    weak var client: JarvisClient?
    var onQuit: (() -> Void)?
    var onStop: (() -> Void)?
    var onToggleVoice: (() -> Void)?
    var onOpenCanvas: (() -> Void)?
    var onConsent: ((Int, Bool) -> Void)?

    private let transcript = NSStackView()
    private var transcriptScroll: NSScrollView!
    private let emptyState = NSView()
    private let input = NSTextField()
    private var composer: NSView!
    private var micBtn: DSButton!
    private var sendBtn: DSButton!
    private var consentHeight: NSLayoutConstraint!
    private let pickerHost = NSStackView()       // holds the pinned-verb chip, if any
    private let picker = ActionPicker()
    private var pinnedVerb: ActionPicker.Verb?
    private var consentId: Int?
    private let consentBar = NSView()
    private let consentLabel = DS.text("", 12, .medium)
    private var verbs: [ActionPicker.Verb] = []
    private var hasMessages = false

    // ── Dual-Brain (ADR-056): per-conversation brain mode. On-device is the resting state; Cloud is a
    // deliberate, temporary escalation that auto-reverts (new conversation / relaunch). ──
    private let brainToggleHost = NSView()   // a 2-segment [🔒 Private | ☁️ Cloud] toggle, rebuilt on switch
    private var cloudMode = false
    private var cloudProvider: CloudProvider { CloudPrefs.provider }
    private var cloudThinkingRows: [Int: NSView] = [:]   // token -> the transient "thinking…" row
    private var cloudIdleTimer: Timer?                    // decays the conversation back to On-device when idle
    private static let cloudIdleRevert: TimeInterval = 300   // 5 min of no cloud use -> return home

    // friendly name · description · SF Symbol per catalog verb (design catalog table)
    private static let meta: [String: (String, String, String)] = [
        "summarize_file": ("Summarize a document", "Condense a local file into key points", "doc.text.magnifyingglass"),
        "read_article": ("Read a web article", "Fetch and read an online page", "globe"),
        "read_file": ("Read a file", "Open and read a local file", "doc.text"),
        "web_lookup": ("Web search", "Search the web for a query", "magnifyingglass"),
        "report_system": ("System report", "A snapshot of this Mac's health", "gauge.medium"),
        "audio_status": ("Audio status", "Current output device and volume", "speaker.wave.2"),
        "network_status": ("Network status", "Connection, signal, and throughput", "wifi"),
        "largest_apps": ("Largest apps", "Apps using the most disk space", "chart.bar"),
        "find_file": ("Find a file", "Locate a file by name", "doc.text.magnifyingglass"),
        "open_app": ("Open an app", "Launch an application", "app.dashed"),
        "open_url": ("Open a URL", "Open a link in your browser", "link"),
        "set_volume": ("Set volume", "Change the system output volume", "speaker.wave.2"),
        "mute": ("Mute", "Silence system audio", "speaker.slash"),
        "empty_trash": ("Empty trash", "Permanently delete trashed items", "trash"),
    ]
    private static let known = ["learn", "ask", "tell", "status", "health", "sentinel", "forget", "restore"]

    // ── layout ──
    override func loadView() {
        let root = LayerView()
        root.wantsLayer = true
        root.bg = DS.contentBG                          // LayerView re-applies on appearance flip (the toggle)
        root.layer?.backgroundColor = DS.contentBG.cgColor

        // transcript (scroll fills, content centered max-width 720)
        transcriptScroll = NSScrollView()
        transcriptScroll.drawsBackground = false; transcriptScroll.hasVerticalScroller = true
        transcriptScroll.translatesAutoresizingMaskIntoConstraints = false
        transcript.orientation = .vertical; transcript.alignment = .centerX; transcript.spacing = 14
        transcript.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        transcript.translatesAutoresizingMaskIntoConstraints = false
        let flip = FlippedClip(); flip.translatesAutoresizingMaskIntoConstraints = false
        flip.addSubview(transcript)
        transcriptScroll.documentView = flip

        buildEmptyState()
        buildComposer()
        consentBar.isHidden = true
        buildConsentBar()

        for v in [transcriptScroll!, emptyState, consentBar, composer!, picker] { root.addSubview(v) }
        emptyState.translatesAutoresizingMaskIntoConstraints = false
        composer.translatesAutoresizingMaskIntoConstraints = false
        consentBar.translatesAutoresizingMaskIntoConstraints = false
        picker.isHidden = true

        NSLayoutConstraint.activate([
            transcriptScroll.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            transcriptScroll.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            transcriptScroll.topAnchor.constraint(equalTo: root.topAnchor),
            transcriptScroll.bottomAnchor.constraint(equalTo: consentBar.topAnchor),
            flip.leadingAnchor.constraint(equalTo: transcriptScroll.contentView.leadingAnchor),
            flip.trailingAnchor.constraint(equalTo: transcriptScroll.contentView.trailingAnchor),
            flip.topAnchor.constraint(equalTo: transcriptScroll.contentView.topAnchor),
            transcript.leadingAnchor.constraint(equalTo: flip.leadingAnchor),
            transcript.trailingAnchor.constraint(equalTo: flip.trailingAnchor),
            transcript.topAnchor.constraint(equalTo: flip.topAnchor),
            transcript.bottomAnchor.constraint(equalTo: flip.bottomAnchor),
            transcript.widthAnchor.constraint(equalTo: transcriptScroll.widthAnchor),

            emptyState.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            emptyState.centerYAnchor.constraint(equalTo: transcriptScroll.centerYAnchor),
            emptyState.widthAnchor.constraint(lessThanOrEqualToConstant: 460),

            consentBar.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            consentBar.widthAnchor.constraint(lessThanOrEqualToConstant: 720),
            consentBar.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -44).withPriority(.defaultHigh),
            consentBar.bottomAnchor.constraint(equalTo: composer.topAnchor, constant: -8),

            composer.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            composer.widthAnchor.constraint(lessThanOrEqualToConstant: 720),
            composer.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -44).withPriority(.defaultHigh),
            composer.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),
        ])
        consentHeight = consentBar.heightAnchor.constraint(equalToConstant: 0)   // collapses when no consent
        consentHeight.isActive = true
        NSLayoutConstraint.activate([                                            // the / picker, above the composer
            picker.leadingAnchor.constraint(equalTo: composer.leadingAnchor),
            picker.trailingAnchor.constraint(equalTo: composer.trailingAnchor),
            picker.bottomAnchor.constraint(equalTo: composer.topAnchor, constant: -6),
        ])
        self.view = root
    }

    private func buildEmptyState() {
        let tile = DS.iconTile("sparkles", tint: DS.accent, side: 56, pt: 26)
        let h = DS.text("What can I do?", 21, .semibold, DS.label)
        let sub = DS.text("Ask me something, or type / to run a job.", 13.5, .regular, DS.label2)
        let chips = NSStackView(views: ["Summarize a document", "System report", "What's slowing my internet?"].map { suggestionChip($0) })
        chips.orientation = .horizontal; chips.spacing = 8
        let col = NSStackView(views: [tile, h, sub, chips])
        col.orientation = .vertical; col.alignment = .centerX; col.spacing = 10
        col.translatesAutoresizingMaskIntoConstraints = false
        emptyState.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: emptyState.leadingAnchor),
            col.trailingAnchor.constraint(equalTo: emptyState.trailingAnchor),
            col.topAnchor.constraint(equalTo: emptyState.topAnchor),
            col.bottomAnchor.constraint(equalTo: emptyState.bottomAnchor),
        ])
    }

    private func suggestionChip(_ s: String) -> NSView {
        DSButton(s, variant: .secondary, size: 12) { [weak self] in
            self?.input.stringValue = s; self?.view.window?.makeFirstResponder(self?.input)
        }
    }

    private func buildComposer() {
        let fieldBorder = DS.dynamic(light: NSColor(white: 0, alpha: 0.16), dark: NSColor(white: 1, alpha: 0.16))
        composer = DS.rounded(bg: DS.fieldBG, radius: 13, border: fieldBorder, borderWidth: 1)
        // all controls are 30×30 (design spec: .input-ctl / .send-btn)
        // the brain toggle sits leftmost: [🔒 On-device] <-> [☁️ Cloud]. A tap escalates/returns.
        brainToggleHost.translatesAutoresizingMaskIntoConstraints = false
        let plus = DSButton(nil, symbol: "plus", variant: .icon, square: 30, radius: 8) { [weak self] in self?.attach() }
        let slash = DSButton(nil, symbol: "line.diagonal", variant: .icon, square: 30, radius: 8) { [weak self] in self?.togglePicker() }
        pickerHost.orientation = .horizontal; pickerHost.spacing = 6
        input.isBordered = false; input.drawsBackground = false; input.focusRingType = .none
        input.font = DS.font(13.5); input.textColor = DS.label
        input.placeholderString = "Ask, or type / to run a job…"
        input.delegate = self; input.target = self; input.action = #selector(submit)
        input.translatesAutoresizingMaskIntoConstraints = false
        input.setContentHuggingPriority(.defaultLow, for: .horizontal)
        micBtn = DSButton(nil, symbol: "mic", variant: .icon, square: 30, radius: 8) { [weak self] in self?.onToggleVoice?() }
        sendBtn = DSButton(nil, symbol: "arrow.up", variant: .primary, square: 30, radius: 9) { [weak self] in self?.submit() }

        rebuildBrainToggle()
        let stack = NSStackView(views: [brainToggleHost, plus, slash, pickerHost, input, micBtn, sendBtn])
        stack.orientation = .horizontal; stack.spacing = 4; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        composer.addSubview(stack)
        NSLayoutConstraint.activate([
            composer.heightAnchor.constraint(equalToConstant: 46),   // FIXED — must not stretch to fill
            stack.leadingAnchor.constraint(equalTo: composer.leadingAnchor, constant: 6),
            stack.trailingAnchor.constraint(equalTo: composer.trailingAnchor, constant: -6),
            stack.centerYAnchor.constraint(equalTo: composer.centerYAnchor),
        ])
        picker.onChoose = { [weak self] v in self?.pinVerb(v) }
    }

    private func buildConsentBar() {
        let bar = DS.rounded(bg: DS.amber.withAlphaComponent(0.16), radius: 10, border: DS.amber.withAlphaComponent(0.4))
        bar.translatesAutoresizingMaskIntoConstraints = false
        let glyph = DS.symbol("pause.circle.fill", 13, .medium, DS.amber)
        consentLabel.textColor = DS.amber; consentLabel.maximumNumberOfLines = 2
        let deny = DSButton("Deny", variant: .secondary, size: 12) { [weak self] in self?.resolveConsent(false) }
        let approve = DSButton("Approve", variant: .pillAccent, size: 12) { [weak self] in self?.resolveConsent(true) }
        approve.layer?.backgroundColor = DS.green.cgColor
        let stack = NSStackView(views: [glyph, consentLabel, NSView(), deny, approve])
        stack.orientation = .horizontal; stack.spacing = 8; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        bar.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -8),
            stack.topAnchor.constraint(equalTo: bar.topAnchor, constant: 7),
            stack.bottomAnchor.constraint(equalTo: bar.bottomAnchor, constant: -7),
        ])
        // mount the styled bar inside the consentBar wrapper
        consentBar.addSubview(bar)
        NSLayoutConstraint.activate([
            bar.leadingAnchor.constraint(equalTo: consentBar.leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: consentBar.trailingAnchor),
            bar.topAnchor.constraint(equalTo: consentBar.topAnchor),
            bar.bottomAnchor.constraint(equalTo: consentBar.bottomAnchor),
        ])
    }

    override func viewDidAppear() {
        super.viewDidAppear()
        if verbs.isEmpty { fetchVerbs() }
        view.window?.makeFirstResponder(input)
    }

    /// Headless preview of the / picker (offline).
    func previewPicker() {
        loadViewIfNeeded()
        verbs = [
            .init(name: "summarize_file", label: "Summarize a document", desc: "Condense a local file into key points", symbol: "doc.text.magnifyingglass", auto: true),
            .init(name: "read_article", label: "Read a web article", desc: "Fetch and read an online page", symbol: "globe", auto: true),
            .init(name: "report_system", label: "System report", desc: "A snapshot of this Mac's health", symbol: "gauge.medium", auto: true),
            .init(name: "open_app", label: "Open an app", desc: "Launch an application", symbol: "app.dashed", auto: false),
            .init(name: "empty_trash", label: "Empty trash", desc: "Permanently delete trashed items", symbol: "trash", auto: false),
        ]
        picker.setVerbs(verbs); input.stringValue = "/"; showPicker("")
    }

    /// Headless preview of General Mode (cloud toggle engaged + a cloud answer with the egress footer).
    func previewCloud() {
        loadViewIfNeeded()
        setCloudMode(true)
        addUser("what's the capital of France?")
        cloudAnswer(["ok": true, "provider": "openai", "text": "Paris is the capital of France."])
    }

    private func fetchVerbs() {
        client?.call("catalog_schema") { [weak self] _, body in
            let acts = (body["actions"] as? [[String: Any]]) ?? []
            let vs: [ActionPicker.Verb] = acts.compactMap { a in
                guard let name = a["name"] as? String else { return nil }
                let m = ChatViewController.meta[name]
                return ActionPicker.Verb(name: name,
                                         label: m?.0 ?? (a["label"] as? String ?? name),
                                         desc: m?.1 ?? (a["label"] as? String ?? ""),
                                         symbol: m?.2 ?? "circle",
                                         auto: (a["autonomous"] as? Bool) ?? true)
            }
            DispatchQueue.main.async { self?.verbs = vs; self?.picker.setVerbs(vs) }
        }
    }

    // ── the `/` picker ──
    private func togglePicker() {
        if !picker.isHidden { hidePicker() }
        else { if input.stringValue.isEmpty { input.stringValue = "/" }; view.window?.makeFirstResponder(input); showPicker("") }
    }
    private func showPicker(_ query: String) { if picker.isHidden { picker.reset() }; picker.filter(query); picker.isHidden = false }
    private func hidePicker() { picker.isHidden = true }
    func controlTextDidChange(_ note: Notification) {
        let s = input.stringValue
        sendBtn.alphaValue = (s.isEmpty && pinnedVerb == nil) ? 0.4 : 1
        if s.hasPrefix("/") && !s.contains(" ") { showPicker(String(s.dropFirst())) }
        else if !picker.isHidden { hidePicker() }
    }
    func control(_ c: NSControl, textView: NSTextView, doCommandBy sel: Selector) -> Bool {
        guard !picker.isHidden else { return false }
        switch sel {
        case #selector(NSResponder.moveUp(_:)): picker.move(-1); return true
        case #selector(NSResponder.moveDown(_:)): picker.move(1); return true
        case #selector(NSResponder.insertNewline(_:)): picker.chooseSelected(); return true
        case #selector(NSResponder.cancelOperation(_:)): hidePicker(); return true
        default: return false
        }
    }
    private func pinVerb(_ v: ActionPicker.Verb) {
        pinnedVerb = v
        hidePicker()
        input.stringValue = ""
        input.placeholderString = "Add the target and timing… e.g. the PRD on my desktop tonight"
        pickerHost.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let chip = DS.rounded(bg: DS.accent.withAlphaComponent(0.12), radius: 7)
        let name = DS.text(v.label, 12, .semibold, DS.accent)
        let x = DSButton(nil, symbol: "xmark", variant: .icon) { [weak self] in self?.unpinVerb() }
        let st = NSStackView(views: [name, x]); st.spacing = 2; st.alignment = .centerY
        st.translatesAutoresizingMaskIntoConstraints = false
        chip.addSubview(st)
        NSLayoutConstraint.activate([
            chip.heightAnchor.constraint(equalToConstant: 24),
            st.leadingAnchor.constraint(equalTo: chip.leadingAnchor, constant: 8),
            st.trailingAnchor.constraint(equalTo: chip.trailingAnchor, constant: -2),
            st.centerYAnchor.constraint(equalTo: chip.centerYAnchor),
        ])
        pickerHost.addArrangedSubview(chip)
        view.window?.makeFirstResponder(input)
    }
    private func unpinVerb() {
        pinnedVerb = nil
        input.placeholderString = "Ask, or type / to run a job…"
        pickerHost.arrangedSubviews.forEach { $0.removeFromSuperview() }
    }

    private var attachedFilePath: String?

    private func attach() {
        let p = NSOpenPanel()
        p.canChooseFiles = true; p.canChooseDirectories = false; p.allowsMultipleSelection = false
        p.message = "Choose a document to evaluate (text or PDF)"
        p.prompt = "Attach"
        if #available(macOS 11.0, *) { p.allowedContentTypes = [.plainText, .text, .pdf, .sourceCode, .json] }
        NSApp.activate(ignoringOtherApps: true)               // accessory apps need this or the panel lags/hides
        guard p.runModal() == .OK, let url = p.url else { return }
        attachFile(url.path)
    }

    private func attachFile(_ path: String) {
        attachedFilePath = path
        pickerHost.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let chip = DS.rounded(bg: DS.accent.withAlphaComponent(0.12), radius: 7)
        let name = DS.text("📎 " + (path as NSString).lastPathComponent, 12, .semibold, DS.accent)
        let x = DSButton(nil, symbol: "xmark", variant: .icon) { [weak self] in self?.clearAttachment() }
        let st = NSStackView(views: [name, x]); st.spacing = 2; st.alignment = .centerY
        st.translatesAutoresizingMaskIntoConstraints = false
        chip.addSubview(st)
        NSLayoutConstraint.activate([
            chip.heightAnchor.constraint(equalToConstant: 24),
            st.leadingAnchor.constraint(equalTo: chip.leadingAnchor, constant: 8),
            st.trailingAnchor.constraint(equalTo: chip.trailingAnchor, constant: -2),
            st.centerYAnchor.constraint(equalTo: chip.centerYAnchor),
        ])
        pickerHost.addArrangedSubview(chip)
        input.placeholderString = "Ask about this file… (or just send to evaluate it)"
        view.window?.makeFirstResponder(input)
    }

    private func clearAttachment() {
        attachedFilePath = nil
        pickerHost.arrangedSubviews.forEach { $0.removeFromSuperview() }
        input.placeholderString = cloudMode ? "Ask \(cloudProvider.display)… (this turn leaves your Mac)"
                                            : "Ask, or type / to run a job…"
    }

    private func sendFileToCloud(_ path: String, _ question: String) {
        guard let client = client else { return }
        let key = CloudKeychain.load(cloudProvider) ?? ""
        if key.isEmpty { setCloudMode(false); ensureKeyThenEngage(); return }
        armCloudIdleRevert()
        let row = cloudThinkingRow()
        client.call("cloud_ask", ["text": question, "file": path,
                                  "key": key, "provider": cloudProvider.rawValue]) { [weak self] ok, body in
            DispatchQueue.main.async {
                guard let self else { return }
                if !ok || (body["status"] as? String) != "thinking" {
                    self.replaceThinking(row, token: nil)
                    self.addAssistant(body["text"] as? String ?? "Couldn't read or send the file.", error: true)
                    return
                }
                if let token = body["token"] as? Int { self.cloudThinkingRows[token] = row }
            }
        }
    }

    private var filePending: [Int: NSView] = [:]            // token -> the transient "reading…" row

    /// Private default: evaluate the attached document with the LOCAL model. The file is read on-device by
    /// the off-loop summary worker (it never leaves the Mac); the summary arrives as a `file_result` event.
    private func sendFileLocal(_ path: String) {
        guard let client = client else { return }
        let row = fileReadingRow((path as NSString).lastPathComponent)
        client.call("file_summarize", ["path": path]) { [weak self] ok, body in
            DispatchQueue.main.async {
                guard let self else { return }
                if !ok || (body["status"] as? String) != "reading" {
                    row.removeFromSuperview()
                    self.addAssistant("📎 " + ((body["text"] as? String) ?? "Couldn't read that file."), error: true)
                    return
                }
                if let token = body["token"] as? Int { self.filePending[token] = row }   // await the event
            }
        }
    }

    private func fileReadingRow(_ name: String) -> NSView {
        let v = bubble("📄 Reading \(name) on-device…", bg: DS.fill(0.05), fg: DS.label3)
        addRow(v, align: .leading)
        return v.superview ?? v
    }

    /// Called by AppDelegate on a `file_result` event — the local summarizer's outcome. Carries the same
    /// private footer as a Tier-2 local answer: it stayed on the Mac, and it's a model summary, not a fact.
    func fileResult(_ body: [String: Any]) {
        loadViewIfNeeded()
        if let token = body["token"] as? Int, let row = filePending.removeValue(forKey: token) {
            row.removeFromSuperview()
        }
        let name = (body["name"] as? String) ?? "the file"
        let text = (body["text"] as? String) ?? ""
        if (body["ok"] as? Bool) == true, !text.isEmpty {
            addRow(fileSummaryView(text), align: .leading)
        } else {
            addAssistant("📄 " + (text.isEmpty ? "The local model couldn't summarize \(name)." : text), error: true)
        }
    }

    private func fileSummaryView(_ text: String) -> NSView {
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 4
        col.translatesAutoresizingMaskIntoConstraints = false
        col.addArrangedSubview(bubble(text, bg: DS.fill(0.07), fg: DS.label))
        col.addArrangedSubview(DS.text("📄 Summarized on-device by the local model · the file never left your Mac",
                                       11, .regular, DS.label3))
        return col
    }

    // ── submit ──
    @objc private func submit() {
        let line = input.stringValue.trimmingCharacters(in: .whitespaces)
        guard let client = client else { return }
        hidePicker()
        if let file = attachedFilePath {                        // a "+"-attached document to evaluate
            let name = (file as NSString).lastPathComponent
            addUser(line.isEmpty ? "📎 Evaluate \(name)" : "\(line)  📎 \(name)")
            input.stringValue = ""; clearAttachment()
            if cloudMode {
                sendFileToCloud(file, line)                     // read + send to the cloud brain -> chat answer
            } else {
                sendFileLocal(file)                             // private default: local model reads it on-device
            }
            return
        }
        if let v = pinnedVerb {                                  // a pinned-verb job
            guard !line.isEmpty else { return }
            addUser("/\(v.label)  \(line)")
            input.stringValue = ""; unpinVerb()
            client.call("intent_parse", ["text": line, "action": v.name]) { [weak self] _, b in
                DispatchQueue.main.async { self?.handleIntent(b) }
            }
            return
        }
        guard !line.isEmpty else { return }
        input.stringValue = ""
        addUser(line)
        if line.hasPrefix("/") {                                 // typed /verb without picking
            let parts = String(line.dropFirst()).split(separator: " ", maxSplits: 1).map(String.init)
            let verb = parts.first ?? "", rest = parts.count > 1 ? parts[1] : ""
            if rest.isEmpty { addAssistant("Add a target, e.g. /\(verb) ~/Desktop/report.pdf tonight", error: false); return }
            client.call("intent_parse", ["text": rest, "action": verb]) { [weak self] _, b in
                DispatchQueue.main.async { self?.handleIntent(b) }
            }
            return
        }
        let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
        let head = parts[0].lowercased(), known = Self.known.contains(head)
        // General Mode: a plain question (not a known REPL command) goes to the cloud brain, off-loop.
        if cloudMode && !known {
            sendToCloud(line); return
        }
        // On-device: a plain question runs the hybrid-retrieval engine (symbolic memory + STAMP). On a
        // grounded derivation we render the "Why" panel; on honest abstention we offer Ask Cloud.
        if !cloudMode && !known {
            sendToRecall(line); return
        }
        let cmd = known ? head : "ask", arg = known ? (parts.count > 1 ? parts[1] : "") : line
        client.call(cmd, arg) { [weak self] _, body in
            DispatchQueue.main.async {
                if let t = body["text"] as? String { self?.addAssistant(t, error: false) }
                if let committed = body["committed"] as? [String], !committed.isEmpty {
                    self?.addAssistant("✓ saved: " + committed.joined(separator: " · "), error: false)
                }
            }
        }
    }

    // ── Dual-Brain: the toggle, the cloud send, and the answer footer ──
    /// ONE toggle button that flips its symbol: 🔒 (Private) <-> ☁️ (Cloud). The mode is named on hover.
    /// Private = subtle/bordered; Cloud = accent-filled (active, "leaving your Mac"). Rebuilt on switch.
    private func rebuildBrainToggle() {
        brainToggleHost.subviews.forEach { $0.removeFromSuperview() }
        let b = DSButton(nil, symbol: cloudMode ? "cloud.fill" : "lock.fill",
                         variant: cloudMode ? .primary : .secondary, square: 32, radius: 8) { [weak self] in self?.toggleBrain() }
        b.toolTip = cloudMode
            ? "Cloud · \(cloudProvider.display) — this turn leaves your Mac. Click for Private."
            : "Private · on-device — nothing leaves your Mac. Click for Cloud."
        b.translatesAutoresizingMaskIntoConstraints = false
        brainToggleHost.addSubview(b)
        NSLayoutConstraint.activate([
            b.leadingAnchor.constraint(equalTo: brainToggleHost.leadingAnchor),
            b.trailingAnchor.constraint(equalTo: brainToggleHost.trailingAnchor),
            b.topAnchor.constraint(equalTo: brainToggleHost.topAnchor),
            b.bottomAnchor.constraint(equalTo: brainToggleHost.bottomAnchor),
        ])
    }

    private func toggleBrain() {
        if cloudMode { setCloudMode(false) }                         // ☁️ -> 🔒 instantly
        else { selectCloud() }                                       // 🔒 -> ☁️ via disclosure + key
    }

    private func selectCloud() {
        if cloudMode { return }
        let go = { [weak self] in self?.ensureKeyThenEngage() }      // disclose once, then key, then engage
        if CloudPrefs.disclosureShown { go() }
        else {
            CloudUI.presentDisclosure(on: view.window, provider: cloudProvider) { accepted in
                guard accepted else { return }
                CloudPrefs.disclosureShown = true; go()
            }
        }
    }

    private func ensureKeyThenEngage() {
        if (CloudKeychain.load(cloudProvider) ?? "").isEmpty {
            CloudUI.presentKeyEntry(on: view.window, provider: cloudProvider) { [weak self] saved in
                if saved { self?.setCloudMode(true) }
            }
        } else { setCloudMode(true) }
    }

    private func setCloudMode(_ on: Bool) {
        cloudMode = on
        loadViewIfNeeded()
        rebuildBrainToggle()                                         // re-highlight the active segment
        if pinnedVerb == nil {
            input.placeholderString = on ? "Ask \(cloudProvider.display)… (this turn leaves your Mac)"
                                         : "Ask, or type / to run a job…"
        }
        cloudIdleTimer?.invalidate(); cloudIdleTimer = nil
        if on { armCloudIdleRevert() }
    }

    /// Cloud is a temporary escalation — after a stretch of no cloud use, decay back to the safe default.
    private func armCloudIdleRevert() {
        cloudIdleTimer?.invalidate()
        cloudIdleTimer = Timer.scheduledTimer(withTimeInterval: Self.cloudIdleRevert, repeats: false) {
            [weak self] _ in self?.setCloudMode(false)
        }
    }

    private func sendToCloud(_ text: String) {
        guard let client = client else { return }
        let key = CloudKeychain.load(cloudProvider) ?? ""
        if key.isEmpty { setCloudMode(false); ensureKeyThenEngage(); return }   // key vanished -> re-prompt
        armCloudIdleRevert()                        // fresh cloud activity resets the decay-home clock
        let row = cloudThinkingRow()
        client.call("cloud_ask", ["text": text, "key": key, "provider": cloudProvider.rawValue]) { [weak self] ok, body in
            DispatchQueue.main.async {
                guard let self else { return }
                if !ok || (body["status"] as? String) != "thinking" {           // rejected before dispatch
                    self.replaceThinking(row, token: nil)
                    self.addAssistant(body["text"] as? String ?? "Couldn't reach the cloud brain.", error: true)
                    return
                }
                if let token = body["token"] as? Int { self.cloudThinkingRows[token] = row }  // await the event
            }
        }
    }

    /// Called by AppDelegate when a `cloud_answer` event arrives (the off-loop reply).
    func cloudAnswer(_ body: [String: Any]) {
        loadViewIfNeeded()
        let token = body["token"] as? Int
        if let token, let row = cloudThinkingRows[token] { replaceThinking(row, token: token) }
        let provider = (body["provider"] as? String).flatMap(CloudProvider.init(rawValue:)) ?? cloudProvider
        if (body["ok"] as? Bool) == true {
            let text = body["text"] as? String ?? ""
            addRow(answerWithFooter(text, provider: provider), align: .leading)
        } else {
            // structured failure -> a recovery bubble, never a crash/hang
            let err = body["error"] as? String ?? "The cloud call failed."
            addAssistant("☁️ " + err, error: true)
        }
    }

    /// A quiet note that the cloud turn became permanent local memory (the Dual-Brain payoff).
    func cloudLearned(_ count: Int) {
        guard count > 0 else { return }
        loadViewIfNeeded()
        let note = DS.text("🧠 \(count) fact\(count == 1 ? "" : "s") added to your local memory",
                           11, .regular, DS.label3)
        note.translatesAutoresizingMaskIntoConstraints = false
        addRow(note, align: .leading)
    }

    // ── On-device 3-tier cascade: Tier 1 vault (recall) -> Tier 2 local 7B (converse) -> Tier 3 Cloud
    // (manual ☁️ toggle). Tier 1 grounds with provenance; on abstain we fall to the private local model. ──
    private var recallPending: [Int: (NSView, String)] = [:]            // token -> (reasoning row, query)

    private func sendToRecall(_ text: String) {
        guard let client = client else { return }
        let reasoning = reasoningRow()                                   // .reasoning (muted, transient)
        client.call("recall", text) { [weak self] _, body in
            DispatchQueue.main.async {
                guard let self else { return }
                // Tier 1 (deterministic vault). A grounded plan returns a fast ack and the answer arrives
                // later as a `recall_result` event; an immediate abstain (no local subgraph) comes here.
                if (body["status"] as? String) == "reasoning", let token = body["token"] as? Int {
                    self.recallPending[token] = (reasoning, text)        // await the off-loop result
                } else {
                    reasoning.removeFromSuperview()
                    self.tryLocal(text)                                 // Tier 1 abstained -> Tier 2 (local 7B)
                }
            }
        }
    }

    /// Called by AppDelegate on a `recall_result` event — the off-loop Stage-4 outcome. Tier 1 grounded ->
    /// the "Why" panel; Tier 1 abstained (no-answer / timeout) -> fall through to Tier 2 (the local 7B).
    func recallResult(_ body: [String: Any]) {
        loadViewIfNeeded()
        let token = body["token"] as? Int ?? -1
        let pending = recallPending.removeValue(forKey: token)
        pending?.0.removeFromSuperview()
        if (body["grounded"] as? Bool) == true {
            let conf = ((body["truth"] as? [String: Any])?["confidence"] as? Double)
                .map { String(format: "%.0f%%", $0 * 100) } ?? ""
            addRow(groundedView(answer: body["answer"] as? String ?? "", conf: conf,
                                provenance: body["provenance"] as? [[String: Any]] ?? []), align: .leading)
        } else {
            tryLocal(pending?.1 ?? "")                                  // Tier 2: the private local model
        }
    }

    // ── Tier 2: the private local 7B (converse), reached when the vault can't ground the question ──
    private var localPending: [Int: NSView] = [:]            // ADR-057: token -> the transient "thinking" row

    private func tryLocal(_ query: String) {
        guard let client = client, !query.isEmpty else { return }
        let thinking = localThinkingRow()
        client.call("ask", query) { [weak self] _, body in
            DispatchQueue.main.async {
                guard let self else { return }
                // ADR-057: the 7B decodes OFF the daemon's loop — a fast `thinking_local` ack now, the
                // answer later as a `local_answer` event. (No model wired -> a synchronous `text` instead.)
                if (body["status"] as? String) == "thinking_local", let token = body["token"] as? Int {
                    self.localPending[token] = thinking
                    return
                }
                thinking.removeFromSuperview()
                let text = (body["text"] as? String) ?? ""
                if text.isEmpty {
                    self.addAssistant("The local model couldn't answer that. Toggle ☁️ for the cloud brain.", error: false)
                } else {
                    self.addRow(self.localAnswerView(text), align: .leading)
                }
            }
        }
    }

    /// Called by AppDelegate on a `local_answer` event — the Tier-2 decode finished off-loop.
    func localAnswer(_ body: [String: Any]) {
        loadViewIfNeeded()
        if let token = body["token"] as? Int, let row = localPending.removeValue(forKey: token) {
            row.removeFromSuperview()
        }
        let text = (body["text"] as? String) ?? ""
        if text.isEmpty {
            addAssistant("The local model couldn't answer that. Toggle ☁️ for the cloud brain.", error: false)
        } else {
            addRow(localAnswerView(text), align: .leading)
        }
    }

    private func localThinkingRow() -> NSView {
        let v = bubble("🧠 Thinking locally…", bg: DS.fill(0.05), fg: DS.label3)
        addRow(v, align: .leading)
        return v.superview ?? v
    }

    /// Tier-2 footer: a private local-model answer is a *guess*, not a vault *fact* — so it's clearly
    /// labelled and (unlike the Tier-1 "Why" panel) carries no expandable sources.
    private func localAnswerView(_ text: String) -> NSView {
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 4
        col.translatesAutoresizingMaskIntoConstraints = false
        col.addArrangedSubview(bubble(text, bg: DS.fill(0.07), fg: DS.label))
        col.addArrangedSubview(DS.text("🧠 Local model · private · no internet — toggle ☁️ for live data",
                                       11, .regular, DS.label3))
        return col
    }


    private func reasoningRow() -> NSView {
        let v = bubble("🔒 Reasoning over your Vault…", bg: DS.fill(0.05), fg: DS.label3)
        addRow(v, align: .leading)
        return v.superview ?? v
    }

    /// The grounded answer with an expandable "Why" panel listing the EXACT historical facts that derived
    /// it (each with its english mirror + when it was learned + confidence). The glass box, rendered.
    private func groundedView(answer: String, conf: String, provenance: [[String: Any]], expanded: Bool = false) -> NSView {
        let col = NSStackView(); col.orientation = .vertical; col.alignment = .leading; col.spacing = 6
        col.translatesAutoresizingMaskIntoConstraints = false
        let n = provenance.count
        // Resting state: a MUTED, collapsed trust footer — no raw logic on screen by default.
        let footer = "🔒 Answered by your Vault · \(n) source\(n == 1 ? "" : "s") used"
        let toggle = DSButton((expanded ? "▾ " : "▸ ") + footer, variant: .quiet, size: 12) { }

        let details = NSStackView(); details.orientation = .vertical; details.alignment = .leading; details.spacing = 4
        details.translatesAutoresizingMaskIntoConstraints = false
        details.isHidden = !expanded
        for p in provenance { details.addArrangedSubview(provenanceRow(p)) }
        // The mathematical conclusion sits quietly UNDER the English proof — demoted, monospaced.
        let proof = conf.isEmpty ? "⊢  \(answer)" : "⊢  \(answer)   ·   \(conf)"
        details.addArrangedSubview(DS.text(proof, 10, .regular, DS.label3, mono: true))

        toggle.onPress = { [weak details, weak toggle] in
            guard let details, let toggle else { return }
            details.isHidden.toggle()
            toggle.titleField?.stringValue = (details.isHidden ? "▸ " : "▾ ") + footer
        }
        col.addArrangedSubview(toggle)
        col.addArrangedSubview(details)
        return col
    }

    private func provenanceRow(_ p: [String: Any]) -> NSView {
        let english = (p["english"] as? String) ?? ""
        let narsese = (p["narsese"] as? String) ?? ""
        let primary = english.isEmpty ? narsese : english           // English mirror first…
        let when = (p["learned_at"] as? Double).map(learnedString) ?? ""
        let conf = (p["confidence"] as? Double).map { String(format: "%.2f", $0) } ?? ""
        let card = DS.rounded(bg: DS.card, radius: 8, border: DS.separator)
        let glyph = DS.symbol("checkmark.seal.fill", 12, .medium, DS.green)
        var rows: [NSView] = [DS.text(primary, 12.5, .medium, DS.label, wrap: true)]
        if !when.isEmpty { rows.append(DS.text(when, 11, .regular, DS.label3)) }
        // …the raw Narsese + numeric confidence demoted to quiet monospaced subtext (the proof underneath).
        let proof = [english.isEmpty ? "" : narsese, conf.isEmpty ? "" : "conf \(conf)"]
            .filter { !$0.isEmpty }.joined(separator: "   ·   ")
        if !proof.isEmpty { rows.append(DS.text(proof, 10, .regular, DS.label3, mono: true)) }
        let textCol = NSStackView(views: rows); textCol.orientation = .vertical; textCol.alignment = .leading; textCol.spacing = 1
        let st = NSStackView(views: [glyph, textCol]); st.orientation = .horizontal; st.spacing = 8; st.alignment = .top
        st.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(st)
        NSLayoutConstraint.activate([
            st.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 9),
            st.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -9),
            st.topAnchor.constraint(equalTo: card.topAnchor, constant: 6),
            st.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -6),
        ])
        return card
    }

    private func learnedString(_ ts: Double) -> String {
        let f = DateFormatter(); f.dateFormat = "MMM d"
        return "learned " + f.string(from: Date(timeIntervalSince1970: ts))
    }

    /// Headless preview of the recall surfaces (Tier-1 grounded "Why" panel + a Tier-2 local answer).
    func previewRecall() {
        loadViewIfNeeded()
        addUser("Why did Solana cause dropped_tx?")
        let prov: [[String: Any]] = [
            ["narsese": "<solana --> timeout>", "english": "SOL hit a connection timeout",
             "confidence": 0.9, "learned_at": Date().timeIntervalSince1970 - 86400 * 6],
            ["narsese": "<timeout --> dropped_tx>", "english": "a timeout drops the transaction",
             "confidence": 0.9, "learned_at": Date().timeIntervalSince1970 - 86400 * 2]]
        addRow(groundedView(answer: "<solana --> dropped_tx>", conf: "81% confident", provenance: prov, expanded: true), align: .leading)
        addUser("write a python script to sort these hashes")
        addRow(localAnswerView("Here's a short script:\n\n    hashes.sort()\n\nThat sorts them in place; use sorted(hashes) for a copy."), align: .leading)
    }

    private func cloudThinkingRow() -> NSView {
        let v = bubble("Thinking… ☁️ \(cloudProvider.display)", bg: DS.fill(0.07), fg: DS.label2)
        addRow(v, align: .leading)
        return v.superview ?? v       // the wrap row inserted by addRow
    }
    private func replaceThinking(_ row: NSView, token: Int?) {
        if let token { cloudThinkingRows[token] = nil }
        row.removeFromSuperview()
    }

    /// An assistant bubble with the honest egress footer: which brain answered, and what stayed local.
    private func answerWithFooter(_ s: String, provider: CloudProvider) -> NSView {
        let b = bubble(s, bg: DS.fill(0.07), fg: DS.label)
        let footer = DS.text("☁️ Answered by \(provider.display) · your memory, habits & files stayed on your Mac",
                             11, .regular, DS.label2)
        footer.translatesAutoresizingMaskIntoConstraints = false
        let col = NSStackView(views: [b, footer])
        col.orientation = .vertical; col.alignment = .leading; col.spacing = 4
        col.translatesAutoresizingMaskIntoConstraints = false
        return col
    }

    private func handleIntent(_ body: [String: Any]) {
        if (body["ok"] as? Bool) != true {
            addAssistant((body["clarify"] as? String) ?? (body["text"] as? String) ?? "I couldn't parse that.", error: true); return
        }
        guard let intent = body["intent"] as? [String: Any], let action = intent["action"] as? String else { return }
        let arg = intent["arg"] as? String ?? ""
        let item: [String: String] = ["action": action, "arg": arg]
        let target = arg.isEmpty ? "" : " — \((arg as NSString).lastPathComponent)"
        if let epoch = resolveEpoch(intent["timing"] as? [String: Any]) {
            client?.call("overnight_schedule_batch", ["items": [item], "run_at": epoch]) { _, _ in }
            addChip(state: "scheduled", title: "\(action)\(target)")
        } else {
            client?.call("overnight_enqueue_batch", [item]) { [weak self] _, _ in self?.client?.call("overnight_start") { _, _ in } }
            addChip(state: "running", title: "\(action)\(target)")
        }
    }

    private func resolveEpoch(_ timing: [String: Any]?) -> Double? {
        guard let t = timing, let kind = t["kind"] as? String else { return nil }
        let value = (t["value"] as? Int) ?? Int((t["value"] as? Double) ?? 0)
        switch kind {
        case "in_minutes": return Date().addingTimeInterval(Double(value) * 60).timeIntervalSince1970
        case "at_clock_hour":
            let cal = Calendar.current; let now = Date()
            var c = cal.dateComponents([.year, .month, .day], from: now); c.hour = value; c.minute = 0; c.second = 0
            var target = cal.date(from: c) ?? now
            if target <= now { target = cal.date(byAdding: .day, value: 1, to: target) ?? target }
            return target.timeIntervalSince1970
        default: return nil
        }
    }

    // ── transcript rows ──
    private func markSeen() { if !hasMessages { hasMessages = true; emptyState.isHidden = true } }
    private func addRow(_ v: NSView, align: NSLayoutConstraint.Attribute) {
        markSeen()
        let wrap = NSView(); wrap.translatesAutoresizingMaskIntoConstraints = false
        wrap.addSubview(v); v.translatesAutoresizingMaskIntoConstraints = false
        v.topAnchor.constraint(equalTo: wrap.topAnchor).isActive = true
        v.bottomAnchor.constraint(equalTo: wrap.bottomAnchor).isActive = true
        v.widthAnchor.constraint(lessThanOrEqualTo: wrap.widthAnchor, multiplier: 0.82).isActive = true
        if align == .leading { v.leadingAnchor.constraint(equalTo: wrap.leadingAnchor).isActive = true }
        else { v.trailingAnchor.constraint(equalTo: wrap.trailingAnchor).isActive = true }
        wrap.widthAnchor.constraint(lessThanOrEqualToConstant: 720).isActive = true
        transcript.addArrangedSubview(wrap)
        wrap.widthAnchor.constraint(equalTo: transcript.widthAnchor, constant: -40).withPriority(.defaultHigh).isActive = true
        DispatchQueue.main.async { [weak self] in self?.scrollToBottom() }
    }
    private func scrollToBottom() {
        guard let scroll = transcriptScroll, let doc = scroll.documentView else { return }
        scroll.contentView.scroll(to: NSPoint(x: 0, y: max(0, doc.bounds.height - scroll.contentView.bounds.height)))
    }
    private func bubble(_ s: String, bg: NSColor, fg: NSColor) -> NSView {
        let v = DS.rounded(bg: bg, radius: 14)
        let t = DS.text(s, 13.5, .regular, fg, wrap: true, selectable: true)
        v.addSubview(t)
        NSLayoutConstraint.activate([
            t.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 11),
            t.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -11),
            t.topAnchor.constraint(equalTo: v.topAnchor, constant: 7),
            t.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -7),
        ])
        return v
    }
    private func addUser(_ s: String) { addRow(bubble(s, bg: DS.accent, fg: DS.onAccent), align: .trailing) }
    private func addAssistant(_ s: String, error: Bool) {
        let bg = error ? DS.red.withAlphaComponent(0.12) : DS.fill(0.07)
        let fg = error ? DS.red : DS.label
        addRow(bubble(s, bg: bg, fg: fg), align: .leading)
    }

    private func addChip(state: String, title: String) {
        let chip = DS.rounded(bg: DS.card, radius: 12, border: DS.separator)
        let glyph = DS.symbol(DS.stateGlyph(state), 14, .medium, DS.stateColor(state))
        let t = DS.text(title, 13, .semibold, DS.label)
        let body = DS.text(state == "scheduled" ? "scheduled" : "running…", 12, .regular, DS.stateColor(state))
        let view = DSButton("View on Canvas ›", variant: .quiet, size: 12) { [weak self] in self?.onOpenCanvas?() }
        let st = NSStackView(views: [glyph, t, body, NSView(), view])
        st.orientation = .horizontal; st.spacing = 9; st.alignment = .centerY
        st.translatesAutoresizingMaskIntoConstraints = false
        chip.addSubview(st)
        NSLayoutConstraint.activate([
            chip.heightAnchor.constraint(equalToConstant: 42),
            st.leadingAnchor.constraint(equalTo: chip.leadingAnchor, constant: 11),
            st.trailingAnchor.constraint(equalTo: chip.trailingAnchor, constant: -9),
            st.centerYAnchor.constraint(equalTo: chip.centerYAnchor),
        ])
        addRow(chip, align: .leading)
    }

    // ── public API (AppDelegate) — may be called before the view is shown, so ensure it's loaded ──
    func append(_ text: String) {
        guard !text.isEmpty else { return }
        loadViewIfNeeded()
        addAssistant(text, error: text.hasPrefix("⚠") || text.hasPrefix("✗"))
    }
    func focusInput() { loadViewIfNeeded(); view.window?.makeFirstResponder(input) }
    func setRecording(_ on: Bool) {
        loadViewIfNeeded()
        micBtn.titleField?.stringValue = on ? "Stop & send" : "Listen"
        micBtn.layer?.backgroundColor = (on ? DS.red : NSColor.clear).cgColor
    }
    func setConnected(_ up: Bool) {
        loadViewIfNeeded()
        input.isEnabled = up
        input.placeholderString = up ? (pinnedVerb == nil ? "Ask, or type / to run a job…" : input.placeholderString)
                                     : "Reconnecting to the engine…"
    }
    func showConsent(_ id: Int, _ prompt: String) {
        loadViewIfNeeded(); consentId = id; consentLabel.stringValue = prompt
        consentBar.isHidden = false; consentHeight.constant = 46
    }
    func clearConsent(_ id: Int) {
        if consentId == id { consentId = nil; consentBar.isHidden = true; consentHeight?.constant = 0 }
    }
    private func resolveConsent(_ ok: Bool) {
        guard let id = consentId else { return }
        consentId = nil; consentBar.isHidden = true; consentHeight.constant = 0; onConsent?(id, ok)
    }
}

/// A flipped clip so the transcript stack grows top-down inside the scroll view.
final class FlippedClip: NSView { override var isFlipped: Bool { true } }
