# ADR-053: The Unified Canvas UI

## Status
**Accepted** (ratified after review). Builds directly on the infrastructure shipped in ADR-031
(overnight queue + briefing), ADR-051 (steady-cadence tick + result visibility), and ADR-052 (the
document-summary offload engine + WAL).

**Review resolutions (binding):**
1. **Three tabs — approved.** `Now / Scheduled / Activity`. The Morning Briefing folds into `Activity`;
   all execution states live in one visual hierarchy (no separate results window).
2. **Scheduling granularity — relative/preset only.** Ship the `Tonight` preset **and** an
   "In X Hours" dropdown (In 1 Hour / In 4 Hours). **No `NSDatePicker` calendar** yet — a full calendar
   adds timezone/localization edge cases for a feature whose real use is "do this while I'm at lunch."
   The Swift side computes an **absolute epoch** (`run_at`) from the preset, so the backend stores a
   bare timestamp and carries zero timezone logic. Graduate to a calendar only if behavior demands it.
3. **`alternatives()` scope — file-path family only** (`summarize_file / read_file / read_article`).
   Patches the exact routing failure the user hit; no universal sibling map until there is proven need.

## Context
The Batch Canvas (ADR-033) was built as an *overnight* composer. Three things since then exposed that
its model no longer matches how the system is used:

1. **The "Commit Queue → queue for when?" incoherence.** The current window has two buttons —
   `Commit Queue` (enqueue, no start) and `▶ Run Now` (enqueue + start, offloaded). `Commit Queue`
   commits to *no defined time*: with no scheduler, those rows sit at `pending` until something calls
   `overnight_start`. The user named this directly: *"queue for when? This is again a terrible UX."*
2. **The product is now real-time.** ADR-052 made heavy work (a 40-chunk PDF summary) run off the loop
   with live `[progress] i/N`. The Canvas is no longer a "set it and read it in the morning" surface;
   it is a place to **get things done now and watch them happen**.
3. **The user's stated shape:** *"a 'Canvas' page by default, then … 'Schedule Tasks' where you can
   upload whatever needs processing, give it a date and time to start … different Tabs within the
   window."*

**The honest gap this ADR must close.** There is currently **no time-based execution trigger of any
kind.** Verified: `OvernightRunner.start()` sets `active` and `advance()` drains `next_pending()`
immediately; `session.tick()` calls `advance()` unconditionally (no idle gate, no `run_at`). So
"overnight" today means only "a queue a human chose to start" — not "runs later." Unifying the Canvas
*requires* introducing the scheduling primitive that both the user ("date and time") and Synapse
("idle hours") asked for. This ADR specifies it.

## Decision

A single **Canvas window with three tabs**, one shared composer, one shared task-state machine, and a
new `run_at` scheduling column on `overnight_queue`.

```
┌─ Canvas ─────────────────────────────────────────────────────────┐
│  [ ● Now ]   [ Scheduled ]   [ Activity ]            (segmented)   │
│ ───────────────────────────────────────────────────────────────  │
│  Actions (palette)   │   Plan / Live state                        │
│   🟢 summarize_file   │   summarize_file (report.pdf)   ▶ 3/12     │
│   🟢 report_system    │   ▓▓▓░░░░░░ per-chunk progress bar          │
│   🟠 empty_trash      │   ─────────────────────────────           │
│   …                   │   report_system            ✅ done  [copy] │
│ ───────────────────────────────────────────────────────────────  │
│   ▶ Run Now            Send to Scheduled ▾ ( Tonight 2am | Pick… ) │
└───────────────────────────────────────────────────────────────────┘
```

- **Tab 1 — `Now` (default).** The real-time surface. Compose a plan from the palette, press **Run
  Now**, watch the state machine animate inline (ADR-052 offload makes this non-blocking). This is the
  "Canvas page by default" the user asked for.
- **Tab 2 — `Scheduled`.** The same composer, but the primary action is **Send to Scheduled** with a
  `run_at`: a preset (*Tonight 2am* = the idle window Synapse means) or **Pick…** (an `NSDatePicker`
  for the explicit date+time the user asked for). Lists upcoming tasks with a relative countdown
  ("in 6h 12m"). Unifies both framings: *overnight* is just the `Tonight` preset of the general
  `run_at` mechanism.
- **Tab 3 — `Activity`.** Completed/failed results + the held-for-approval ledger (folds in the
  Morning Briefing, ADR-031, so there is one place results live). Selectable result panels (ADR-051).

One composer, one state machine, three lenses. The Canvas is the window; the tabs are *when/where*,
not *different apps*.

---

### Vector 1 — The State Machine UI

The backend already emits everything the UI needs: `overnight_progress` events `{id, action, status,
detail}` and `overnight_status` rows `{id, action, arg, status, result}`. Status values are
`pending | running | done | failed | held`. The UI is a **pure projection** of that state — it invents
no state of its own (consistent with the dumb-client rule, ADR-033).

```
                 enqueue
                    │
                    ▼
   ┌──────────┐  start / run_at due   ┌──────────┐
   │ QUEUED   │ ────────────────────▶ │ RUNNING  │
   │ ⏳ grey   │                        │ ▶ blue   │
   └──────────┘                        └────┬─────┘
        │ (mutating action)                 │ [progress] {i,n}
        ▼                                    ▼ (offloaded heavy task)
   ┌──────────┐                        ┌──────────┐
   │ HELD     │                        │ WORKING  │  "chunk i/N" + determinate bar
   │ ⏸ amber  │                        │ ▓▓▓░░ 3/12│
   └────┬─────┘                        └────┬─────┘
        │ approve in Activity               ├────────────▶ ┌──────────┐
        ▼                                    │ result       │ DONE     │ ✅ + result panel [copy]
   (runs, → DONE/FAILED)                     └────────────▶ │ FAILED   │ ❌ + error panel + Retry ▾
                                               error        └──────────┘
```

Visual contract per state (one row component, color + glyph + optional body):

| State    | Glyph / color        | Body shown                                          |
|----------|----------------------|-----------------------------------------------------|
| QUEUED   | ⏳ tertiary grey      | — (action + arg only)                               |
| RUNNING  | ▶ system blue        | indeterminate spinner until first `[progress]`      |
| WORKING  | ▓ determinate bar    | `chunk i/N`, bar = i/N (drives off `detail`)        |
| DONE     | ✅ system green       | selectable result panel (the summary), `[copy]`     |
| FAILED   | ❌ system red         | selectable error panel + **Retry ▾** (Vector 3)     |
| HELD     | ⏸ system amber       | "needs approval — open Activity"                    |

Transitions are **event-driven, not timer-guessed**: each `overnight_progress` re-renders exactly the
one row it names (by `id`); the 1 s `overnight_status` poll is the reconciliation fallback (and stops
2 ticks after `active=false`, per ADR-051). `WORKING` is the only state with a determinate bar because
it is the only one with a known denominator (`n` from the offload protocol).

### Vector 2 — The Execution Modes (the Now / Scheduled boundary)

The boundary is **when execution begins**, and it is expressed as a per-batch property, not two
unrelated buttons:

| | **Run Now** (Tab 1) | **Send to Scheduled** (Tab 2) |
|---|---|---|
| Trigger | immediate (`overnight_start` now) | `run_at` timestamp reached during `tick()` |
| Intended for | "I want this now and will watch it" | "do this tonight / at 9pm / while I'm away" |
| Heavy tasks | offloaded (ADR-052) — UI stays live | offloaded identically; UI need not be open |
| Where you watch | inline in the `Now` tab, live | `Activity` tab / Morning Briefing afterward |
| Closing the window | n/a (you're watching) | fine — daemon runs it headless |

**New backend primitive (the dependency this ADR introduces):** add `run_at REAL DEFAULT NULL` to
`overnight_queue`. `NULL` = "manual / Run Now" (today's behavior, unchanged). Non-null = scheduled.
`next_pending()` becomes time-aware: `WHERE status='pending' AND (run_at IS NULL OR run_at <= :now)`.
`session.tick()` auto-activates the runner when a scheduled task comes due (`run_at <= now`). The
existing `reset_running()` crash-safety and the offload seam are unchanged — Scheduled inherits the
ADR-052 engine for free.

**Honest scope flag:** *Tonight 2am* presumes the Mac is awake at 2am; a sleeping machine fires the
task on next wake, not at the wall-clock time. That is an inherent limitation of a local-first daemon
(no cloud cron). The UI must state "runs at 2am if the Mac is awake, else on wake" rather than imply a
guarantee — truthfulness over polish.

### Vector 3 — The Error Surface (interacting with FAILED, and retrying with a different tool)

This is the vector that turns the v1.16.4 *honest-failure* fix into a *recoverable* one. Today a FAILED
row shows its `[ERROR …]` string (ADR-051 result panel) but is a dead end — the user can read why it
failed but cannot act on it.

**The failure taxonomy we already produce** (the error string is the design input):
- *Wrong tool for the input* — `read_article` on a local path → `[ERROR: … is a local file … use
  summarize_file]` (ADR's web-routing fix). The error literally names the right tool.
- *Right tool, unreadable input* — `summarize_file` on a scanned/image-only PDF → `⚠ … has no
  extractable text (it may be a scanned/image-only PDF)`.
- *Right tool, transient* — model/subprocess hiccup → `[ERROR: summarization failed: …]`.

**The mechanism.** Every FAILED row exposes two inline affordances beneath its error panel:

```
 ❌ read_article (PRD.pdf) — failed
 ┌─────────────────────────────────────────────────────────────┐
 │ [ERROR] "PRD.pdf" is a local file, not a web page. To        │
 │ process a document, use "summarize a document".              │
 └─────────────────────────────────────────────────────────────┘
            [ ↻ Retry ]   [ Change tool ▾ ]      ← summarize_file
                                  └─ read_file        (siblings that take the same arg)
                                     read_article
```

1. **↻ Retry** — re-enqueue the *same* `{action, arg}` (for transient failures). New row, fresh state
   machine.
2. **Change tool ▾** — re-enqueue the *same `arg`* against a **sibling action** from a small,
   curated compatibility map keyed by argument type. For a file path: `summarize_file ↔ read_file ↔
   read_article`. The menu is **pre-seeded with the tool the error names** when the error is a routing
   error (so the one-click fix for the local-path case is literally the top item). This is the
   "retry with a different tool" Synapse specified, made one click.

The compatibility map lives in the daemon (a new `catalog`-adjacent `alternatives(action, arg)`
helper), not the Swift UI — the client stays dumb and renders whatever alternatives the daemon offers,
exactly as it renders the palette's autonomous/held tags today. Retrying a *held*/mutating sibling
still routes through the consent path; the error surface can never escalate privilege.

---

## Consequences
- The incoherent `Commit Queue` button is retired; "later" always has a *when* (`run_at`), closing the
  user's complaint.
- The Morning Briefing stops being a separate window — it becomes the `Activity` tab, so results live
  in one place.
- **New dependency to build:** the `run_at` column + time-aware `next_pending()` + tick auto-activate +
  the `alternatives()` helper. The state machine and offload engine already exist; this ADR is mostly
  *UI projection + one scheduling primitive*, not new concurrency.
- Risk surfaced honestly: scheduled execution is best-effort against machine sleep; the UI must say so.

## Open questions for review
1. **Three tabs or two?** The user said two (Canvas + Schedule). I propose a third (`Activity`) to
   absorb the Morning Briefing rather than leave results split across two windows. Approve or collapse
   `Activity` back into the briefing window?
2. **Scheduling granularity:** ship only the `Tonight` preset first (matches Synapse's "idle hours"),
   or the full `NSDatePicker` date+time immediately (matches the user's literal request)?
3. **`alternatives()` scope:** start with the file-path family only (`summarize_file / read_file /
   read_article`), or define sibling sets for every arg type now?
