# ADR-031: Overnight batch queue + persistent held-ledger + Morning Briefing

## Status
Accepted & live-verified. Adds an unattended batch processor with a hard read-only safety boundary and
a durable held-action ledger. Suite 408 → **417**. Tagged **v1.6.0**.

## Context
Goal: queue work before sleep and wake to it done — or to a clean approval list. The naïve version is
dangerous on two axes: (1) giving a local 7B unattended write/GUI access for 8 hours invites hours of
mis-clicking after one layout shift; (2) the existing consent machine, if a 3 AM action opened a card,
would `sweep()` it to **auto-deny** after its 120 s TTL ([consent_service.py](../../src/service/consent_service.py)) —
so a night of work would silently expire to *denied*, not stash. (The earlier "it freezes the loop"
worry was wrong — the select loop never blocks on consent; the real gap is the 120 s auto-deny.)

Scope was set with the user: **mechanism-only, explicit-commit.** No implicit chat scanning
(conversation history isn't persisted anywhere — verified across every `CREATE TABLE`), and no overnight
7B task-planning. Queued tasks are **concrete catalog actions**; the runner is deterministic.

## Decision
**Safety spine (a mathematically hard boundary, not a policy the model can talk its way past):**
- `overnight.safe_autonomous(action)` runs unattended **only** read-only catalog kinds
  (`{"diag","query"}`, non-confirm). Everything else — every `argv` (system-config), `nav`/`ax`/`agent`
  (GUI), and any `confirm` (destructive) — is **HELD**. An action can't become autonomous by omission:
  unknown kind → held (default-deny).
- Held actions execute **only on explicit morning approval** — the briefing's approve → `perform`. The
  click *is* the consent gate, so we never bend `consent_service`.
- The held-ledger is **durable** (sqlite on `jarvis.db`), surviving a 3 AM daemon recycle — unlike the
  in-memory consent ledger.

**Components:**
- New domain module **`src/overnight/`** (per [01-architecture.md](../../standards/01-architecture.md) —
  a `storage/` folder was rejected): `classify.py` (the pure predicate), `store.py`
  (`OvernightQueue` + `HeldLedger`, mirroring `habits/store.py`), `__init__.py` with `__all__`.
- **`service/overnight_runner.py`** — `OvernightRunner`: advances **one task per `session.tick()`**
  (the `_drive_agent`/`propose_due` pattern), so it never monopolises the single-threaded select loop.
  Pops a task → classify → `perform` (safe) or `hold` (rest). **No LLM in the loop.** `start()` calls
  `reset_running()` so a crash-orphaned task self-heals to pending instead of zombie-locking.
- **`service/session.py`** — dispatch commands `overnight_enqueue` / `overnight_start` /
  `overnight_status` / `briefing` / `briefing_resolve` (the last runs an approved held action now).
- **`ui/MorningBriefingView.swift`** — AppKit popover (a third "🌅 Morning Briefing…" right-click item,
  beside ADR-030's "🧠 Habits…"): a **Completed** list + a **Held** checklist with Approve/Deny.
  Enqueue/start are exposed as daemon commands (+ `ChatView.known`); a polished queue editor is future.

## Consequences
- **Gained:** an unattended batch processor that is safe by construction — read-only work runs, anything
  with consequences waits behind one morning click, and nothing is lost to a restart.
- **Live-verified (in-process, real stores):** enqueue `find_file` (safe) + `empty_trash` (held) +
  `report_system` (safe); start; drain → both safe ran, `empty_trash` sits in the held-ledger; the row
  **survived a store reopen** (simulated restart); approving it executed it.
- **Tests:** +9 — `classify` (read-only autonomous, everything-else/unknown held), `store` (queue
  lifecycle, `reset_running`, **held survives reopen**), `runner`+dispatch (safe runs / rest held /
  drains; enqueue validates against the catalog; briefing-approve performs). Suite **417**.
- **Honest limits:**
  - **It only orchestrates actions that exist.** The read-only surface today is `find_file` +
    `report_system`, so unattended output is thin until read-only **work primitives** (read/summarize/
    draft-to-scratchpad) land — the explicit next ADR, not this one.
  - **Implicit-propose deferred** — needs a durable conversation log that doesn't exist.
  - **No scheduler** — you `overnight_start` manually at bedtime; auto-trigger is future.
  - Single-threaded: `advance()` briefly blocks the loop during one action (fine overnight).
  - Consent machine, habit/sentinel brains, and the frozen cognitive engine are untouched.

## Alternatives Considered
- **Patch `consent_service` with an `overnight` mode** that stashes instead of TTL-denying: rejected —
  the runner classifying per-action is cleaner and leaves the verified consent machine untouched.
- **Implicit chat-history scan to build the queue:** deferred — no conversation persistence exists; it'd
  be a separate, larger build, and inferring an unattended mandate is the highest-risk autonomy.
- **A generic `storage/` directory for the new stores:** rejected — violates domain decomposition; the
  stores live in the `overnight/` domain module behind its `__init__.py`.
