# ADR-003: Headless daemon + line/JSON IPC over a unix-domain socket

## Status
Accepted

## Context
The product is moving from a terminal-only tool to a talking, learning companion with a native
SwiftUI menu-bar UI (and later push-to-talk voice). The terminal console previously *embedded* the
entire reasoning core (two ONA brains, the LLM channel, gate, grounding, executor, sentinel) and did
its own I/O. If the SwiftUI app also embedded or ad-hoc-drove the core, we would duplicate reasoning
logic across clients and let UI concerns leak into the brain — and the brain could only be tested by
fighting a GUI event loop.

We need a single home for reasoning that multiple front-ends share, while preserving the strict
local-first / offline mandate and the existing single-threaded, lock-free concurrency model.

## Decision
Extract the reasoning core into a **headless daemon** (`service/`) that owns all state and runs the
existing single-threaded `select()` loop. Clients talk to it over a **unix-domain socket** using a
**line-delimited JSON protocol** with three frame kinds:

- `request` `{t:"req",id,cmd,arg}` and `response` `{t:"res",id,ok,body}` — correlated by `id`;
- `event` `{t:"evt",kind,body}` — unsolicited server→client push (sentinel alerts, intervention
  prompts).

The console becomes a thin `Client`; the SwiftUI app will be a second thin client. The codec
(`protocol.py`) is pure, so the contract and every command are testable without a socket or GUI.

## Consequences
- **Easier:** one source of truth for reasoning; clients are dumb; the brain is testable headlessly
  (`test_service.py` drives a real daemon over a real socket, no GUI); adding the SwiftUI client is a
  transport exercise, not a re-implementation.
- **Harder / new:** a daemon lifecycle to manage (the console spawns one if absent and terminates it
  on exit); interactive flows (`learn` escalation, `act` confirm) become explicit two-message
  exchanges instead of in-process callbacks; sqlite connections are thread-bound, so the daemon must
  be built and served in the same thread/process (it is — one process, one thread).
- **Preserved:** local-first (a filesystem socket, never a TCP port); the single-threaded, lock-free
  model (one `select()` loop multiplexes clients + the sensor pipe); the security crucible (the
  executor and its closed catalog are unchanged, now reached through `dispatch`).

## Alternatives Considered
- **Local web app (HTTP/WebSocket on localhost):** rejected — heavier, a TCP port even on loopback,
  and a browser destination breaks the ambient-companion goal (see the UI decision).
- **Embed the core in each client (Python in console, re-impl in Swift):** rejected — duplicates
  reasoning logic and pollutes the brain with UI concerns; the exact thing this ADR prevents.
- **gRPC / a message bus:** rejected — adds a dependency and build complexity for a single local
  producer/consumer; stdlib `socket` + `json` is sufficient and keeps the footprint tiny.
- **TCP socket on 127.0.0.1:** rejected in favor of a unix-domain socket — no port, filesystem
  permissions, and zero chance of off-host exposure.
