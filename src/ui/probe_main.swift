// Headless verification of the Swift<->Python IPC bridge: connect, round-trip a few requests, print
// results, exit. No GUI. Built into a separate `jarvis-probe` binary so the bridge is provable
// exactly like the Python seam test. Usage: jarvis-probe <socket-path>
import Foundation

@main
struct Probe {
    static func main() {
        let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/nars-jarvis.sock"
        guard let client = JarvisClient(path: path) else {
            FileHandle.standardError.write("PROBE-FAIL: could not connect to \(path)\n".data(using: .utf8)!)
            exit(1)
        }
        client.onEvent = { kind, body in print("EVENT \(kind): \(body)") }
        client.start()

        func show(_ label: String, _ r: (Bool, [String: Any])?) {
            guard let (ok, body) = r else { print("\(label): TIMEOUT"); exit(2) }
            print("\(label): ok=\(ok) body=\(body)")
        }

        show("tell", client.callSync("tell", "<a --> b>."))
        show("ask", client.callSync("ask", "<a --> b>?"))
        show("status", client.callSync("status"))
        print("PROBE-OK")
        client.close()
    }
}
