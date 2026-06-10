# ADR-033: The Block-Based Batch Canvas (+ Clear Completed)

## Status
Accepted & verified. Adds the dedicated authoring surface for overnight batches and the hygiene command
to flush the briefing. Suite 430 → **435**. Tagged **v1.8.0**.

## Context
ADR-031/032 made the overnight engine real, but composing a batch meant typing `overnight_enqueue` one
line at a time, and the Morning Briefing's Done list grew without bound (queue 50 docs → tomorrow's
briefing is unusable). ADR-033 delivers the workspace the user sketched — a standalone window to compose
a batch visually — plus the **Clear Completed** flush that the high-throughput ingestion makes mandatory.

## Decision
Two ratified design calls:
- **Flat list of independent tasks** (not a piped dependency chain). The runner already is one — a failed
  task marks `failed` and the rest continue. Piping (output of A → arg of B, abort-on-failure) is a large
  escalation that reintroduces fragility for little gain (`summarize_file` already does read→summarize);
  deferred.
- **Click-to-add composer** (not drag-and-drop). The repo had **zero** drag/drop infrastructure;
  click-to-add + a native file picker delivers the same outcome (compose, see tags, commit) with far less
  AppKit surface and keeps Swift a dumb client. True drag-drop + a file-drop Context Tray are deferred.

**Python (the daemon stays the single source of truth — Swift hardcodes no business logic):**
- `actions/catalog.py` `schema()` → `{name, label, kind, takes_arg}` for non-AX actions (pure; no
  overnight semantics, preserving dependency direction).
- `overnight/store.py` `OvernightQueue.purge_done()` → deletes `done`/`failed` rows (never pending/held).
- `service/session.py` dispatch:
  - `catalog_schema` → filters `schema()` to overnight-appropriate kinds `{work,query,diag,argv,nav}`
    (excludes `ax`/`agent`/`habit`) and annotates each with `autonomous = safe_autonomous(...)`. This is
    where the autonomy call lives (session imports both `actions` + `overnight`) → the mockup's *mixed*
    palette (work/query/diag = Autonomous, argv/nav = Held).
  - `overnight_enqueue_batch` → `[{action,arg}]`; validates each against the catalog (unknown → rejected,
    never queued); returns `{queued, rejected}`.
  - `briefing_dismiss_done` → `purge_done()`.

**Swift (AppKit, click-to-add):**
- `ui/BatchCanvasView.swift` — a real **window** (not a popover): a left palette from `catalog_schema`
  (each button chipped 🟢 Autonomous / 🟠 Held), a center plan built by clicking (each row = badge +
  name + argument field + a native "Choose…" `NSOpenPanel` for file args + remove ×), and Commit /
  Commit+Start → `overnight_enqueue_batch`.
- `ui/AppDelegate.swift` — a **retained** `NSWindow` (`isReleasedWhenClosed=false`, `makeKeyAndOrderFront`
  + `NSApp.activate`, since `.accessory` apps must activate) opened from a new "🗂 Batch Canvas…" item.
- `ui/MorningBriefingView.swift` — a **"Clear Completed"** button → `briefing_dismiss_done`.

## Consequences
- **Gained:** a visual, safe authoring workspace — compose a mixed batch, see exactly what's Autonomous
  vs Held *before* committing, and keep the briefing clean. The Swift side renders only what the daemon
  describes, so the safety classification can never drift from the backend.
- **Verified:** suite **435** (+5: `purge_done` keeps pending/held; `schema` shape + no-ax; dispatch
  `catalog_schema` mixed/no-ax, `overnight_enqueue_batch` queues-valid/rejects-unknown,
  `briefing_dismiss_done` purges). Live over the socket: `catalog_schema` returns the mixed palette;
  a batch of `summarize_file` + `empty_trash` commits (2 queued); start → summarize ran autonomously,
  empty_trash held; `briefing_dismiss_done` cleared the Done list.
- **Honest limits:** the window's click/compose UX is **human-verified** (a GUI window can't be asserted
  headlessly); all backend commands are socket-verified. Deferred: true drag-drop + file-drop Context
  Tray, piped dependency chains, the NL→blocks proposer. Safety boundary unchanged — the canvas can
  *compose* Held actions, but they still run only on explicit morning approval.

## Alternatives Considered
- **Piped dependency chain:** rejected for v1 — graph + inter-task data flow + abort semantics + edge UI,
  and it reintroduces chain fragility. Flat list is deterministic and sufficient.
- **True drag-and-drop canvas:** rejected for v1 — zero existing infra; click-to-add + NSOpenPanel is the
  same outcome at a fraction of the AppKit risk.
- **Swift reads the catalog / computes the tags:** rejected — the daemon annotates `autonomous` so the UI
  stays logic-free and the classification has one source of truth.
