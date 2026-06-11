# ADR-049: Context Orchestration Layer — bootstrap (verified `set_volume`)

## Status
Accepted & live-verified. First increment of the Context Orchestration Layer (the tiered actuation
backend that unblocks ADR-050, the Passive Behavioral-Learning Loop). Full design rationale:
[`docs/prd/ADR-049-context-orchestration-brief.md`](../prd/ADR-049-context-orchestration-brief.md).
Suite 507 → **513**.

## Context
JARVIS could *read* the machine (diagnostics, sensors, web research) but barely *act* on it — an
"actuation gap" that makes passive habit-learning pointless (you'd learn routines JARVIS can't perform).
The orchestration layer closes that gap. Before any code, the design was pressure-tested across five
implementation vectors; four locked parameters came out of it (below).

## Decision (this increment: the synchronous bootstrap)
Ship **`set_volume <0-100>`** — the safest possible first muscle and the proof of the orchestration
loop `actuate → verify → report`:
- **Zero-TCC actuate:** `osascript -e 'set volume output volume N'` (StandardAdditions, *no* `tell
  application` → no Automation prompt). Verified live: no TCC dialog appeared.
- **Inline verify (read-back):** reuses the existing `diagnostics.parse_volume_settings` to confirm the
  state landed (within macOS's ~6.25 quantization step), and reports the *actual* value.
- **Ungated:** a single reversible primitive is below the consent threshold (ADR-020 proportional
  consent), so it runs immediately — no prompt for a volume change.
- New `kind="orchestrate"` in the catalog → `actions/orchestrate.py` (closed set, dispatch by name);
  `run.perform` routes it; pure-tested offline (6 tests) + live-verified (set→verify→restore).

**The four locked parameters** (carried from Phase-1 deconstruction; bind every future increment):
1. **Tri-state, event-driven verification** — `VERIFIED` / `FAILED` / `PENDING`. Synchronous-readable
   states (volume) verify inline; async states (app launch, Focus) verify off the **`NSWorkspace`
   notification stream the sentinel already feeds** — *no hardcoded timeouts*; `PENDING` never reports
   failure or triggers an undo (a slow cold launch is not a failure).
2. **Cached capability registry** — TCC grants + shortcut existence read O(1) from `jarvis.db`,
   refreshed on boot / idle-tick / post-consent. **No process-spawning pre-flight on the hot path**;
   the verify step is the staleness backstop.
3. **Declarative atomicity** — a routine declares `atomic` + per-step `required`/`optional`. A failed
   required step in the *reversible atomic core* rolls that core back to baseline (recorded inverses);
   irreversible steps (launches) are best-effort, ordered first. (No blanket never-rollback; no
   blanket rollback — declared.)
4. **Proportional consent** — ungated for single reversible primitives; one consolidated prompt per
   multi-step routine (≥~3 steps) or irreversible/high-impact step. KPIs measured at routine grain.

## Consequences
- **Gained:** the first verified actuator; the orchestration loop proven end-to-end on real hardware
  with zero TCC friction; a clean `orchestrate` extension point for the next primitives.
- **Explicitly NOT done yet (honest scope):** the **async barrel** (`open_app`/deep-link with
  `NSWorkspace`-event verification), the cached capability registry, declarative routines + atomicity
  rollback, and the consent gate for multi-step routines. Those are the next increments — the four
  parameters above are their binding spec. This increment is the *synchronous barrel only*, and does
  not by itself prove the async engine (that's deliberately the next build, with live sensor testing).
- **Go-gate to ADR-050 (unchanged):** over a 2-week window, verified-actuation ≥95% / consent-accept
  ≥75% / ≥5 routine-relevant actions / 0 unreversed misfires, surfaced in `health`.

## Alternatives Considered
- **Deep-link as the bootstrap** — rejected: no clean read-back to *verify* (you'd race app-launch
  state). Volume is the only primitive with a matching zero-TCC reader already in the tree.
- **Build both barrels at once** — rejected: the async-event-verify needs careful select-loop / sensor
  integration and live testing; bundling it with the sync bootstrap would ship it untested.
- **Consent-gate every actuation uniformly** — rejected (Vector 4): friction > manual effort for a
  reversible primitive; consent is proportional to risk/complexity per ADR-020.
