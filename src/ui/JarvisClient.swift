// JarvisClient — the Swift side of the IPC bridge. Connects to the headless Python daemon over a
// unix-domain socket and speaks the same line-delimited JSON protocol as service/protocol.py.
//
// Strictly a transport: zero reasoning, zero state beyond in-flight request bookkeeping. A single
// background reader thread drains the socket, completes pending requests by id, and forwards
// unsolicited events (sentinel alerts / intervention prompts) to `onEvent`. Mirrors the Python
// `Client`. See service/README.md and ADR-003.
import Darwin
import Foundation

final class JarvisClient {
    private let fd: Int32
    private var inbuf = Data()
    private var nextId = 0
    private var pending: [Int: (Bool, [String: Any]) -> Void] = [:]
    private let lock = NSLock()
    /// Called on a background thread with (kind, body) for every unsolicited event frame.
    var onEvent: ((String, [String: Any]) -> Void)?
    /// Called once, on a background thread, when the socket drops (daemon restart/exit) so the app
    /// can reconnect instead of silently zombieing (ADR-017).
    var onDisconnect: (() -> Void)?
    private var didDisconnect = false

    init?(path: String) {
        fd = socket(AF_UNIX, SOCK_STREAM, 0)
        if fd < 0 { return nil }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        if bytes.count >= MemoryLayout.size(ofValue: addr.sun_path) { Darwin.close(fd); return nil }
        withUnsafeMutablePointer(to: &addr.sun_path) {
            $0.withMemoryRebound(to: UInt8.self, capacity: bytes.count) { dst in
                for (i, b) in bytes.enumerated() { dst[i] = b }
                dst[bytes.count] = 0
            }
        }
        let size = socklen_t(MemoryLayout<sockaddr_un>.size)
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, size) }
        }
        if rc != 0 { Darwin.close(fd); return nil }
    }

    /// Start the background reader. Call once after init.
    func start() {
        Thread.detachNewThread { [weak self] in self?.readLoop() }
    }

    private func readLoop() {
        var chunk = [UInt8](repeating: 0, count: 65536)
        while true {
            let n = read(fd, &chunk, chunk.count)
            if n <= 0 { signalDisconnect(); return }    // daemon closed / error -> let the app reconnect
            inbuf.append(contentsOf: chunk[0..<n])
            while let nl = inbuf.firstIndex(of: 0x0A) {
                let line = inbuf.subdata(in: inbuf.startIndex..<nl)
                inbuf.removeSubrange(inbuf.startIndex...nl)
                if let obj = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] {
                    dispatch(obj)
                }
            }
        }
    }

    private func signalDisconnect() {
        lock.lock(); let first = !didDisconnect; didDisconnect = true; lock.unlock()
        if first { onDisconnect?() }                 // fire at most once per client
    }

    private func dispatch(_ frame: [String: Any]) {
        switch frame["t"] as? String {
        case "res":
            let id = frame["id"] as? Int ?? -1
            lock.lock(); let done = pending.removeValue(forKey: id); lock.unlock()
            done?(frame["ok"] as? Bool ?? false, frame["body"] as? [String: Any] ?? [:])
        case "evt":
            onEvent?(frame["kind"] as? String ?? "", frame["body"] as? [String: Any] ?? [:])
        default:
            break
        }
    }

    /// Send a request; `completion` fires on the reader thread when the correlated response arrives.
    func call(_ cmd: String, _ arg: Any = "", completion: @escaping (Bool, [String: Any]) -> Void) {
        lock.lock(); nextId += 1; let id = nextId; pending[id] = completion; lock.unlock()
        let frame: [String: Any] = ["t": "req", "id": id, "cmd": cmd, "arg": arg]
        guard var data = try? JSONSerialization.data(withJSONObject: frame) else { return }
        data.append(0x0A)
        data.withUnsafeBytes { _ = write(fd, $0.baseAddress, $0.count) }
    }

    /// Blocking convenience (used by the headless probe / off-main callers).
    @discardableResult
    func callSync(_ cmd: String, _ arg: Any = "", timeout: TimeInterval = 60) -> (Bool, [String: Any])? {
        let sem = DispatchSemaphore(value: 0)
        var out: (Bool, [String: Any])?
        call(cmd, arg) { ok, body in out = (ok, body); sem.signal() }
        return sem.wait(timeout: .now() + timeout) == .success ? out : nil
    }

    func close() {
        lock.lock(); didDisconnect = true; lock.unlock()   // intentional teardown -> don't trigger reconnect
        Darwin.close(fd)
    }
}
