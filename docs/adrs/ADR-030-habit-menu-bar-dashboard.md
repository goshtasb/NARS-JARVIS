# ADR-030: The Habit Brain menu-bar dashboard (telemetry for the field test)

## Status
Accepted & live-verified. Gives the ADR-026/027/028 Habit Brain a native macOS presence. The
**cognitive engine is frozen at v1.4.0** — this adds only a read-only projection + a route to the
existing `forget`. Tagged **v1.5.0**.

> ADR-029 (cloud/Google Drive capability) was proposed and **dropped** — with local NVMe space
> unconstrained, network egress would have broken the local-first/air-gapped invariant for no gain
> (see ADR-015). The number is intentionally skipped.

## Context
We are entering a deliberate multi-day **field test**: let the daemon run during real work and gather
the one thing we've never had — empirical proof that the gate arms under messy human behavior, not just
synthetic in-process ramps. That test needs an **instrument**. ADR-027's `list_habits` only exists down
the conversational `[[DO:]]` path, i.e. behind a 7B LLM round-trip — too slow/nondeterministic for a
glanceable dashboard, and the UI had no way to *reach* habits directly (the daemon `dispatch` table had
no habit command).

## Decision
A native menu-bar dashboard that lists every tendency/habit with its live `[Armed]`/`[Learning]
(seen ~N×)` state and a per-row **Forget** button.

**IPC: the Swift UI uses the existing Unix-socket frame protocol — it does NOT read the SQLite file.**
Direct DB access was rejected because:
1. **Forget would corrupt the brain.** `HabitLoop.forget()` craters the ONA term (`{0.0 0.9}`) *and*
   deletes the row; a raw DB delete leaves the live brain still believing the habit (re-arms on next
   replay). Forget must go through the daemon.
2. **It would break ADR-027 encapsulation.** `[Armed]`/`[Learning]`/`seen` are computed by the *same*
   `gate_passes`/`evidence_count` the proposal tick uses. Reading SQLite forces the UI to either show
   raw `frequency`/`confidence` (forbidden — confidence ≠ probability) or reimplement the gate math in
   Swift (guaranteed drift).
3. **DB-lock risk** — a second reader process invites `SQLITE_BUSY` against the daemon's live writer.
4. **The transport already exists** — `JarvisClient` speaks the protocol; the app is already a thin
   client. Direct SQLite would fork a second integration path that bypasses the boundary.

**Surface (Python — projection + IPC only, no cognitive change):**
- `service/habit_loop.py`: `snapshot() -> list[dict]` — structured per-row view reusing
  `gate_passes`/`evidence_count`; rows carry `{key, description, scope: habit|tendency,
  state: armed|learning, seen, arms_at}` and **never** raw frequency/confidence.
- `service/session.py`: two `dispatch` commands — `"habits"` → `{"rows": snapshot()}` (deterministic,
  bypasses the LLM); `"habit_forget"` → `HabitLoop.forget(arg)` (distinct from the memory `"forget"`).

**Surface (Swift — thin client, AppKit to match the codebase, not SwiftUI):**
- `ui/HabitsView.swift`: `HabitsViewController` fetches `"habits"` on open, renders rows + a Forget
  button (the row key rides on the button's `identifier`); Forget calls `"habit_forget"` then refreshes.
- `ui/AppDelegate.swift`: a second transient popover opened from a new "🧠 Habits…" right-click menu
  item, sharing the already-wired `client`. **Refresh = fetch-on-open** (habits change on a daily
  timescale → polling/event-push is unwarranted).

## Consequences
- **Gained:** a glass-box dashboard that makes the field test observable — watch `[Learning] seen ~3×`
  become `[Armed]`, and prune a mis-learned habit with one click (safely cratered + purged).
- **Live-verified:** `dispatch("habits")` returns structured rows over the socket with no LLM round-trip;
  `dispatch("habit_forget", key)` craters the ONA term and removes the row.
- **Tests:** +2 — `snapshot()` structured/no-raw-math; the two dispatch handlers route through
  `HabitLoop` (forget craters ONA, not a raw delete). Suite 406 → **408**.
- **Honest limits:** no live ticking while the popover is open (daily timescale; fetch-on-open shows the
  latest each time). Swift has no unit-test harness here — UI is verified by build + live interaction.
  Engine remains frozen: zero changes to gate math, quantization, observation, or proposal.

## Alternatives Considered
- **Swift reads the SQLite file directly:** rejected — corrupts brain/DB on forget, leaks/duplicates
  NARS math, risks `SQLITE_BUSY`, forks the IPC boundary (see Decision §1–4).
- **Reuse `list_habits` via the LLM path:** rejected — slow, nondeterministic, returns a prose blob
  unsuited to per-row Forget buttons.
- **Event-pushed live updates:** deferred — unjustified for a daily-timescale signal; fetch-on-open suffices.
