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
    private let pickerHost = NSStackView()       // holds the pinned-verb chip, if any
    private let picker = ActionPicker()
    private var pinnedVerb: ActionPicker.Verb?
    private var consentId: Int?
    private let consentBar = NSView()
    private let consentLabel = DS.text("", 12, .medium)
    private var verbs: [ActionPicker.Verb] = []
    private var hasMessages = false

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
        let root = NSView()
        root.wantsLayer = true
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

        for v in [transcriptScroll!, emptyState, consentBar, composer!] { root.addSubview(v) }
        emptyState.translatesAutoresizingMaskIntoConstraints = false
        composer.translatesAutoresizingMaskIntoConstraints = false
        consentBar.translatesAutoresizingMaskIntoConstraints = false

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

            consentBar.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 20),
            consentBar.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -20),
            consentBar.bottomAnchor.constraint(equalTo: composer.topAnchor, constant: -8),

            composer.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 20),
            composer.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -20),
            composer.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),
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
        composer = DS.rounded(bg: DS.fieldBG, radius: 13, border: DS.separator)
        let plus = DSButton(nil, symbol: "plus", variant: .icon) { [weak self] in self?.attach() }
        let slash = DSButton(nil, symbol: "slash.circle", variant: .icon) { [weak self] in self?.togglePicker() }
        pickerHost.orientation = .horizontal; pickerHost.spacing = 6
        input.isBordered = false; input.drawsBackground = false; input.focusRingType = .none
        input.font = DS.font(13.5); input.textColor = DS.label
        input.placeholderString = "Ask, or type / to run a job…"
        input.delegate = self; input.target = self; input.action = #selector(submit)
        input.translatesAutoresizingMaskIntoConstraints = false
        input.setContentHuggingPriority(.defaultLow, for: .horizontal)
        micBtn = DSButton("Listen", symbol: "mic", variant: .secondary, size: 12) { [weak self] in self?.onToggleVoice?() }
        sendBtn = DSButton(nil, symbol: "arrow.up", variant: .primary) { [weak self] in self?.submit() }

        let stack = NSStackView(views: [plus, slash, pickerHost, input, micBtn, sendBtn])
        stack.orientation = .horizontal; stack.spacing = 7; stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        composer.addSubview(stack)
        NSLayoutConstraint.activate([
            composer.heightAnchor.constraint(greaterThanOrEqualToConstant: 46),
            stack.leadingAnchor.constraint(equalTo: composer.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: composer.trailingAnchor, constant: -7),
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
        if picker.isVisible { picker.hide() }
        else { input.stringValue = "/"; view.window?.makeFirstResponder(input); picker.update(anchor: composer, query: "") }
    }
    func controlTextDidChange(_ note: Notification) {
        let s = input.stringValue
        sendBtn.alphaValue = (s.isEmpty && pinnedVerb == nil) ? 0.4 : 1
        if s.hasPrefix("/") && !s.contains(" ") {
            picker.update(anchor: composer, query: String(s.dropFirst()))
        } else if picker.isVisible { picker.hide() }
    }
    func control(_ c: NSControl, textView: NSTextView, doCommandBy sel: Selector) -> Bool {
        guard picker.isVisible else { return false }
        switch sel {
        case #selector(NSResponder.moveUp(_:)): picker.move(-1); return true
        case #selector(NSResponder.moveDown(_:)): picker.move(1); return true
        case #selector(NSResponder.insertNewline(_:)): picker.chooseSelected(); return true
        case #selector(NSResponder.cancelOperation(_:)): picker.hide(); return true
        default: return false
        }
    }
    private func pinVerb(_ v: ActionPicker.Verb) {
        pinnedVerb = v
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

    private func attach() {
        let p = NSOpenPanel(); p.canChooseFiles = true; p.canChooseDirectories = false; p.allowsMultipleSelection = false
        if p.runModal() == .OK, let url = p.url {
            let cur = input.stringValue
            input.stringValue = cur.isEmpty ? url.path : cur + " " + url.path
            view.window?.makeFirstResponder(input)
        }
    }

    // ── submit ──
    @objc private func submit() {
        let line = input.stringValue.trimmingCharacters(in: .whitespaces)
        guard let client = client else { return }
        picker.hide()
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
        wrap.widthAnchor.constraint(equalTo: transcript.widthAnchor, constant: -40).isActive = true
        DispatchQueue.main.async { [weak self] in self?.scrollToBottom() }
    }
    private func scrollToBottom() {
        guard let doc = transcriptScroll.documentView else { return }
        transcriptScroll.contentView.scroll(to: NSPoint(x: 0, y: max(0, doc.bounds.height - transcriptScroll.contentView.bounds.height)))
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

    // ── public API (AppDelegate) ──
    func append(_ text: String) {
        guard !text.isEmpty else { return }
        addAssistant(text, error: text.hasPrefix("⚠") || text.hasPrefix("✗"))
    }
    func focusInput() { view.window?.makeFirstResponder(input) }
    func setRecording(_ on: Bool) {
        micBtn.titleField?.stringValue = on ? "Stop & send" : "Listen"
        micBtn.layer?.backgroundColor = (on ? DS.red : NSColor.clear).cgColor
    }
    func setConnected(_ up: Bool) {
        input.isEnabled = up
        input.placeholderString = up ? (pinnedVerb == nil ? "Ask, or type / to run a job…" : input.placeholderString)
                                     : "Reconnecting to the engine…"
    }
    func showConsent(_ id: Int, _ prompt: String) { consentId = id; consentLabel.stringValue = prompt; consentBar.isHidden = false }
    func clearConsent(_ id: Int) { if consentId == id { consentId = nil; consentBar.isHidden = true } }
    private func resolveConsent(_ ok: Bool) {
        guard let id = consentId else { return }
        consentId = nil; consentBar.isHidden = true; onConsent?(id, ok)
    }
}

/// A flipped clip so the transcript stack grows top-down inside the scroll view.
final class FlippedClip: NSView { override var isFlipped: Bool { true } }
