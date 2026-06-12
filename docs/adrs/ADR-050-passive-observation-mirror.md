# ADR-050: Passive-observation mirror — "What I've noticed about your computer use"

## Status
Accepted & live-verified. The first slice of the Passive Behavioral-Learning Loop — **observation +
mirror only** (no NARS integration, no acting on patterns). Re-sequenced *ahead* of the orchestration
layer's remaining steps on the user's repeated signal. Suite 513 → **517**.

## Context
The project's charter is "an assistant that learns your habits on your machine," but JARVIS only ever
learned from commands routed *through* it — it was blind to how the user actually uses the computer.
The user said this five+ times across a session: *"it does not observe as I use the computer."*
Verified true and structural: the Sentinel **watches** app focus in real time (its sensor streams every
foreground switch) but **discards** it — it consumes the stream for attention-fragmentation detection
and persisted nothing as usage history (`focus_blocks: 0`, no usage table).

The ratified roadmap put passive observation *last*, behind the whole orchestration layer, on the
argument "a silent profile is worthless without actuation." That critique applies to *auto-acting* on a
hidden profile — **not** to a **visible mirror**. A high-fidelity "here's how you actually spend your
day," shown back to you, has intrinsic value as self-knowledge, independent of any actuation. So the
mirror is decoupled and built now.

## Decision
Stop discarding the Sentinel's focus stream; aggregate it into a Cognitive Identity view.
- **`usage_events`** table in `jarvis.db`: one row per observed app switch — **bundle id + coarse
  category + timestamp ONLY**. Content-blind by construction (never a window title, URL, or document),
  the same privacy line the Sentinel already enforces. Lazily pruned (30-day retention) so it stays
  bounded. `SentinelLoop._handle` records each switch via `SentinelStore.record_usage`.
- **`sentinel/usage.py`** (pure): `summarize_usage(events, now)` → dwell-per-app (consecutive-switch
  deltas, single dwell capped at 30 min for idle), dwell-per-category, busiest hour, switch count →
  a human "What I've noticed" string. Friendly app names from a small known-bundle map + cleaned-bundle
  fallback (modern bundle ids are opaque, e.g. Cursor = `com.todesktop.<hash>`).
- **`usage` daemon command** → the summary; surfaced as a **"What I've noticed"** section atop the
  🧠 Cognitive Identity panel (`HabitsView.swift`), beside Routine Cadence and Persona Constraints.

**Deliberately NOT in scope (the rest of the passive loop, still gated behind orchestration):** feeding
these patterns into the NARS Habit Brain, *acting* on them, or proactive offers. This slice only
*shows* what it sees — read-only, no NARS, no actuation, no new TCC.

## Consequences
- **Gained:** JARVIS finally reflects the user's real machine use. Live-verified end-to-end — a fresh
  daemon logged a switch and the mirror rendered ("Most of your time: Cursor (dev); busiest around
  4 PM; learned passively from which app is in front — never your screen contents"). The Cognitive
  Identity is no longer empty for a research-heavy user who rarely routes actions through JARVIS.
- **Cost paid:** the `usage_events` log grows with use (bounded by 30-day prune). Friendly names are
  best-effort for unknown opaque bundles (a follow-up can have the sensor emit `localizedName`).
- **Re-sequencing:** this partly reverses "orchestration before observation." Justified: the mirror is
  the lowest-risk, highest-felt-value increment (no actuation surface), and it directly resolves the
  user's central frustration. The *acting-on-patterns* half still waits for the orchestration go-gate.

## Alternatives Considered
- **Hold to orchestration-first** — rejected: the user repeatedly signaled the mirror is what they
  value, and it has standalone worth (the "landfill" critique was about auto-acting, not showing).
- **Category-only summary** (no app names) — rejected as too coarse; bundle-level with friendly-name
  best-effort is recognizably "you," which is the point.
- **Feed the stream to NARS now** — rejected (out of scope): risks the attention-buffer flood the
  Sentinel pipeline guards against, and isn't needed to *show* the mirror. Quantized NARS ingestion is
  the later increment.
