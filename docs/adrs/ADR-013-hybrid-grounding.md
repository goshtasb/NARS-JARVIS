# ADR-013: Hybrid grounding — pre-commit reconciliation of memory vs procedural habits

## Status
Accepted — first slice of ADR-007 open item (d) (hybrid grounding). Fixes the split-brain divergence
found during ADR-012. Builds on ADR-011 (`sentinel_beliefs`) and ADR-012 (the read-only store handle +
category vocabulary).

## Context
ADR-012 surfaced a concrete split-brain bug: a casual conversational request ("please auto-hide my
IDE") is auto-saved (ADR-008) into `memories` as a preference that **contradicts the procedural
autonomy gate** in `sentinel_beliefs` (where the user had *declined* dev auto-hiding). The two stores
disagree and the contradictory memory becomes injectable "ground truth." The deterministic gate still
governs *actions* (not a safety hole), but the declarative brain is poisoned.

## Decision
A **synchronous pre-commit grounding hook** on the auto-memory write path. Autonomy control belongs to
the gate (the single source of truth), not free-form conversational memory — so a would-be memory that
asserts control over a sentinel-governed category is **dropped at pre-commit**, and the **deterministic
layer owns that turn's reply** with the authoritative habit state.

- **Detection (`context/grounding.py`, pure):** `_category_of(fact)` returns a governed category only
  when the text expresses **JARVIS auto-hiding** it — requires BOTH an auto-hide-intent pattern
  (`auto-hide`, `hide … when distracted/fragmenting`, `JARVIS … hide … apps/tools`) AND a known
  category synonym (reverse of `habits._FRIENDLY`). `conflicting_habit(fact, beliefs)` returns
  `(category, enabled)` only if a **confident** habit governs that category (expectation ≥ 0.85 or
  ≤ 0.15); else None → saves normally.
- **Action:** in `Jarvis.converse`, partition the would-be saves; persist only the non-conflicting
  ones; if a conflict fired, **return `grounding_notice(category, enabled)`** — *"Auto-hiding {X} apps
  is controlled by your learned settings — it's currently {disabled}; … approve it when the sentinel
  next offers, to {enable} it."* Because `converse` is non-streaming we hold the full reply before
  emitting, so the LLM's possibly-agreeing prose is suppressed — the user never sees the hallucinated
  agreement (the lesson from ADR-012: the LLM is a text calculator, the control plane speaks for
  control-plane matters).
- **Wiring:** `sentinel_beliefs_provider=self._sentinel_store.beliefs` reuses the ADR-012 read-only
  `SentinelStore` handle — no new store, no ONA round-trip; the hook is a cheap in-memory scan.

Synchronous (not an async background sweep): an async reconciliation leaves a divergence window where
the poisoned memory is live and injectable — exactly the bug. Pre-commit prevents the bad state from
ever existing, mirroring `ContradictionGuard`'s read-before-write shape.

## Consequences
- **Gained:** the split-brain bug is closed at the source — conversational memory can no longer hold a
  preference that contradicts the authoritative gate; the user gets a clear, deterministic explanation
  + the path to change it (consent).
- **Visible, not silent:** the drop is explained in the reply (honors "visible & correctable"); the
  user can act on it.
- **Accepted scope / deferrals (explicit):**
  - **No interactive consent round-trip in v1** — the SwiftUI app has no confirm UX and `converse`
    returns plain text; routing a `[y/n]` into `_feed_consent` from conversation is a follow-on.
  - **Procedural habits only** — knowledge-brain auto-memory contradictions are partly covered by the
    `ContradictionGuard` on the opportunistic `learn` path; full coverage is future work.
  - **No LLM output/hallucination grounding** — the original ADR-007(d) capstone (ONA flags a
    contradictory *answer*) remains deferred.
- **False-drop risk:** mitigated by requiring auto-hide intent AND a confident governing habit; a
  non-match saves as before; a finite synonym set fails toward saving (pre-ADR-013 behavior), never
  toward a wrong drop. Edge case: if a turn both saves a normal fact and triggers a conflict, the
  notice owns the reply and the normal save's `(Saved:)` ack is omitted (the fact is still saved).

## Alternatives Considered
- **Async background reconciliation sweep:** rejected — divergence window + a new subsystem.
- **Silently drop the contradictory save:** rejected — invisible, breeds distrust; surface it.
- **Append the notice to the LLM's agreeing prose:** rejected — self-contradictory whiplash; the
  deterministic layer owns the reply on a control-plane conflict.
