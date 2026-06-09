# ADR-028: Multi-variable habit context (Phase 2 — weekday + foreground app)

## Status
Accepted & live-verified. Adds context fidelity to the ADR-026 Habit Brain. Suite 396 → **406**.
Tagged **v1.4.0**.

## Context
ADR-026/027 learn + surface *temporal* habits (`h16_mute` — "mute around 4:00 PM"). But a 4pm habit
that fires on a Sunday movie instead of a Friday Zoom standup feels uncalibrated. The signal was too
low-fidelity to earn a permanent autonomous home.

**The core tension (stated honestly):** the user's instinct — record both a bare temporal term AND a
fuller contextual term — is right, but naïvely *firing* the coarse term over-generalizes. A user who
only mutes-at-4pm-in-Zoom still feeds bare `h16_mute` on every such event, so the bare term arms too —
and would then fire in Spotify. The fix therefore lives in the **proposal rule**, not just in the terms.

## Decision
Add two **coarse** context dimensions and gate firing on specificity.

- **`habits/quantize.py` (pure):**
  - `day_type(dt) -> "weekday" | "weekend"` — binary on purpose. Clustering days makes evidence recur
    fast enough to cross the 0.85 floor; per-day (`mon`/`tue`/…) would fragment `w` and never converge.
  - `app_slug(app) -> "app_zoom"` — lowercased, term-sanitized; `""` when the foreground is unknown.
  - `context_key(bucket, action, arg, day_type, app) -> "h16_mute_weekday_app_zoom"` — extends
    `habit_key` with the coarse context; still term-safe and recurring.
  - `describe_habit` gains optional context → *"mute in Zoom on weekdays around 4:00 PM"*.

- **`habits/store.py`:** PRAGMA-gated migration adds `day_type`, `app`, `scope` columns (`scope` =
  `'base'` temporal vs `'context'` full) to pre-Phase-2 DBs in place. `record(...)` carries them;
  `for_context(bucket, day_type, app)` returns the `scope='context'` candidates for the current context.

- **`service/habit_loop.py`:**
  - Inject `foreground: Callable[[], str]` (the focused app).
  - **`observe` (no starving):** record evidence at **two independent grains** — the **base** term
    (`habit_key`, `scope=base`) **always**, and the **context** term (`context_key`, `scope=context`)
    *when the app is known*. Each is its own ONA term + row, accumulating on its own schedule.
  - **`propose_due` (specificity-gated — the fidelity fix):** current ctx = (bucket, `day_type(now)`,
    `app=foreground()`).
    - **app known** → candidates = `for_context(...)` only. Bare temporal terms are **not** fired
      (this is what stops the Spotify over-fire).
    - **app unknown** (no AX/focus signal) → fall back to ADR-026 base-term firing.
    Cooldown unchanged (once per day-bucket, per term).
  - **`describe`** labels the grains: `[habit]` for context rows, `[tendency]` for base rows.

- **`service/session.py`:** passes `foreground=lambda: self._ax_app` (last focused non-JARVIS app from
  `ax_context`; empty when Accessibility isn't granted → base-only learning, documented).

## Consequences
- **Gained:** habits are context-calibrated — *"mute in Zoom on weekdays at 4pm"* — and **do not fire in
  Spotify**. The specificity invariant holds *by construction* (firing requires a context-matched armed
  term), while no grain starves (each is a clean, independently-fed ONA term).
- **Live-verified (real ONA + store):** ramping `mute` with `foreground='Zoom'` for 8 days armed BOTH
  grains (f=1.000, c=0.889); `list_habits` showed *"[habit] mute in Zoom on weekdays around 4:00 PM —
  [Armed]"* and *"[tendency] mute around 4:00 PM — [Armed]"*; `propose_due` with foreground=`Zoom`
  proposed (1 consent), with foreground=`Spotify` was **silent** (0 consent).
- **Tests:** +10 — quantize (`day_type` binary, `app_slug` sanitize, `context_key` shape + term-safety,
  `describe_habit` w/ context); store (`for_context` filter, migration adds columns on a pre-Phase-2
  table); convergence (context term arms like a coarse term, against real ONA); habit_loop (no-starving
  both grains, **Zoom-proposes / Spotify-silent**, unknown-app fallback to base). Suite **406**.
- **Honest limits:**
  - Contextual habits need the app signal (Accessibility granted + a focus change). No signal →
    base-only learning + the ADR-026 temporal fallback for firing.
  - Binary weekday is deliberately coarse (fidelity vs convergence speed). Per-day / part-of-day is a
    later refinement only if evidence volume supports it.
  - Two grains per event roughly doubles habit rows, but each is a clean ONA term; introspection labels
    them so the user isn't confused by a "tendency" vs a fireable "habit".
  - Still **propose-only** behind the ADR-020 consent gate — a mis-learned context habit costs one Deny.
