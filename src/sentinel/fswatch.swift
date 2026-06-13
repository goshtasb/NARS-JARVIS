// Passive Context Ingestion — the FSEvents edge (v1.24.0, Sprint 1).
//
// Watches one user-designated folder (argv[1]) via the native, coalesced, event-driven FSEvents stream
// — no polling, near-zero idle cost. The flood-proofing is structural, not algorithmic:
//   1. DENY substring + extension allowlist run INSIDE the kernel callback, so noise (node_modules, .git,
//      build dirs, binaries) dies on the dispatch queue before it ever allocates an IPC payload.
//   2. Survivors go into a deduping Set hard-capped at MAX_SET; on overflow the Set collapses to a single
//      {"rescan":"<dir>"} marker — an O(1) memory ceiling regardless of filesystem entropy.
//   3. A debounced flush (re-armed on every event) emits one JSON line on stdout per quiescence window.
// The callback never touches stdout; the debounce timer drains the bounded Set. So a 100k-file dump
// produces, at the pipe: nothing (all denied) or one rescan marker — never the storm.
import Foundation

setbuf(stdout, nil)  // unbuffered: the parent daemon's select() sees each flush line immediately

let DENY = ["/node_modules/", "/.git/", "/.svn/", "/.hg/", "/build/", "/dist/", "/.next/", "/target/",
            "/__pycache__/", "/.cache/", "/Caches/", "/DerivedData/", "/.venv/", "/venv/", "/vendor/",
            "/.Trash/", "/Pods/", "/.gradle/", "/bin/", "/obj/"]
let KEEP_EXT: Set<String> = ["txt", "md", "markdown", "rst", "pdf", "py", "js", "ts", "tsx", "jsx",
                             "swift", "c", "h", "cpp", "hpp", "cc", "go", "rs", "java", "kt", "rb",
                             "json", "yaml", "yml", "toml", "html", "css", "sh", "sql"]
let MAX_SET = 1000           // hard cap: overflow collapses to a coarse rescan marker (O(1) memory)
let DEBOUNCE = 3.0           // seconds of quiescence before a flush
let watchDir = (CommandLine.arguments.count > 1 ? CommandLine.arguments[1]
                : NSString(string: "~/Desktop/VaultTest").expandingTildeInPath) as String

// All state below is touched ONLY on `q` (the FSEvents dispatch queue + the debounce work item run on it),
// so it's a serial single-owner — no locks.
let q = DispatchQueue(label: "fswatch")
var dirty = Set<String>()
var overflow = false
var pendingFlush: DispatchWorkItem?

func keep(_ path: String) -> Bool {
    for d in DENY where path.contains(d) { return false }
    return KEEP_EXT.contains((path as NSString).pathExtension.lowercased())
}

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       let line = String(data: data, encoding: .utf8) {
        print(line)   // one JSON object per line on stdout
    }
}

func doFlush() {   // runs on q
    if overflow {
        emit(["rescan": watchDir])
        overflow = false; dirty.removeAll(); return
    }
    if dirty.isEmpty { return }
    emit(["paths": Array(dirty)])
    dirty.removeAll()
}

func scheduleFlush() {   // runs on q; re-arm the debounce on every event burst
    pendingFlush?.cancel()
    let item = DispatchWorkItem(block: doFlush)
    pendingFlush = item
    q.asyncAfter(deadline: .now() + DEBOUNCE, execute: item)
}

// The kernel's own "you missed events" signals: it coalesced/dropped, or wants us to rescan a subtree.
// Treat any of these like a Set overflow -> emit the coarse rescan marker so we never silently lose files.
let DROP_FLAGS = FSEventStreamEventFlags(kFSEventStreamEventFlagKernelDropped
                                         | kFSEventStreamEventFlagUserDropped
                                         | kFSEventStreamEventFlagMustScanSubDirs)

let callback: FSEventStreamCallback = { _, _, count, eventPaths, eventFlags, _ in
    // kFSEventStreamCreateFlagUseCFTypes -> eventPaths is a CFArray of CFString.
    let paths = unsafeBitCast(eventPaths, to: NSArray.self) as? [String] ?? []
    for i in 0..<count {
        if (eventFlags[i] & DROP_FLAGS) != 0 { overflow = true; break }   // kernel dropped -> rescan
        guard i < paths.count else { break }
        let p = paths[i]
        guard keep(p) else { continue }                  // noise dies here, before any allocation
        if dirty.count >= MAX_SET { overflow = true; break }   // collapse to a coarse rescan marker
        dirty.insert(p)
    }
    scheduleFlush()
}

let flags = UInt32(kFSEventStreamCreateFlagUseCFTypes
                   | kFSEventStreamCreateFlagFileEvents
                   | kFSEventStreamCreateFlagNoDefer)
guard let stream = FSEventStreamCreate(nil, callback, nil, [watchDir] as CFArray,
                                       FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 1.0, flags) else {
    FileHandle.standardError.write("fswatch: could not create FSEvent stream for \(watchDir)\n".data(using: .utf8)!)
    exit(1)
}
// Best-effort kernel-level exclusion of the two noisiest roots (the stream also can't exclude nested ones,
// which is why the edge DENY substring filter is the real guarantee).
FSEventStreamSetExclusionPaths(stream, [watchDir + "/node_modules", watchDir + "/.git"] as CFArray)
FSEventStreamSetDispatchQueue(stream, q)
guard FSEventStreamStart(stream) else {                  // fail fast — never run silently with no events
    FileHandle.standardError.write("fswatch: FSEventStreamStart failed for \(watchDir)\n".data(using: .utf8)!)
    FSEventStreamInvalidate(stream); FSEventStreamRelease(stream)
    exit(1)
}
dispatchMain()
