# service

## Overview
The headless JARVIS daemon and its IPC contract (Phase 1 of the companion build). The reasoning
core runs as a **single-threaded daemon** behind a line-delimited JSON protocol over a
**unix-domain socket**; every UI is a thin client. This decouples reasoning from presentation: the
terminal console (today) and the SwiftUI menu-bar app (Phase 2) are both dumb clients of the same
surface, so brain logic is never duplicated in — or polluted by — a UI, and the whole brain is
**testable headlessly** with no GUI event loop. See [ADR-003](../../docs/adrs/ADR-003-headless-daemon-ipc.md).

## Usage
```bash
python3 -m service          # run the daemon (binds the unix socket; loads local models if wired)
```
```python
from service import Client
c = Client(); c.connect()
ok, body = c.call("tell", "<tim --> duck>.")     # request/response, correlated by id
ok, body = c.call("ask", "Is Tim a bird?")       # English -> grounded, cited answer
c.set_event_handler(lambda kind, body: ...)      # async push: "alert", "intervention"
c.pump()                                          # drain events when your select() says readable
```
The console (`src/console.py`) is the reference client: it spawns the daemon if one isn't running,
then multiplexes the keyboard and the socket.

## Key Components
- **`protocol.py`** — pure codec. Three frame kinds: `request`/`response` (id-correlated) and
  unsolicited `event` (server→client push). `LineBuffer` reassembles frames across partial reads.
- **`session.py`** — `Session`: the headless command plane. Builds the core and exposes
  `dispatch(cmd, arg) -> (ok, body)` returning plain JSON-able data; emits async work via `on_event`.
- **`sentinel_loop.py`** — `SentinelLoop`: the flow sentinel (second isolated brain, sensor, funnel,
  0.85 burn-in gate, interventions, focus/calibration KPI), driven by the daemon's select loop.
- **`server.py`** — `Daemon`: single-threaded select() over the listen socket + clients + sensor
  pipe; ticks the M2 system sentinel on timeout; broadcasts events to all clients.
- **`client.py`** — `Client`: blocking `call()` (dispatches events seen while waiting) + `pump()`.
- **`wiring.py`** — optional local LLM/embedder sourcing with offline fallbacks.
- **`voice.py`** — push-to-talk STT/TTS: whisper.cpp as a `select()`-multiplexed child (`WhisperJob`,
  never blocks the loop) + offline `say` (`speak`). Only a WAV *path* crosses the socket; the daemon
  routes the transcript through the normal command pipeline and speaks the reply. See ADR-005.
- **`autonomy.py`** — NARS-gated autonomy (pure): the procedural appropriateness belief, asymmetric
  consent weights (yes `{1.0 0.5}`, no `{0.0 0.9}`), and the two-condition gate (confidence ≥ 0.85
  AND favorable expectation). `SentinelLoop` queries it before acting and feeds consent back on each
  y/n; ~6 approvals earn autonomy, one decline revokes it. See ADR-006.
- **Habit dashboard commands** (ADR-030) — `habits` returns a structured snapshot
  (`{"rows": [{key, description, scope, state, seen, arms_at}]}`) of the learned habits with the NARS
  math already encapsulated (no raw frequency/confidence); `habit_forget <key>` routes through
  `HabitLoop.forget` so the ONA term is cratered *and* the row purged. Both bypass the LLM — they back
  the menu-bar dashboard (`ui/HabitsView.swift`).
- **Kill switch** — the `shutdown` command stops the whole daemon cleanly (both brains, sensor,
  actuator); surfaced as the UI's Emergency Stop and the console's `shutdown`.

## Dependencies
`brain`, `jarvis`, `language`, `memory`, `execution`, `sentinel` (all via their public interfaces).
Standard library only for transport (`socket`, `select`, `json`) — no network, no extra packages.

## Related ADRs
[ADR-001](../../docs/adrs/ADR-001-adopt-and-adapt-engineering-standards.md),
[ADR-003](../../docs/adrs/ADR-003-headless-daemon-ipc.md),
[ADR-030](../../docs/adrs/ADR-030-habit-menu-bar-dashboard.md).
