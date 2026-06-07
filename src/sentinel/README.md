# sentinel

## Overview
The local observation pipeline (C3 / M2). It watches the OS and emits discretized Narsese events;
all flooding-prevention math lives in the pure functional core; psutil/watchdog are a thin shell. If
this pipeline leaked, ONA's 40-slot attention buffer would flood and the reasoner would collapse — so
the cores are designed so that **an idle machine emits zero events** and floods are mathematically
unreachable.

> **M2 was observe-only. The V2 Flow Sentinel (below) acts — but only on explicit human consent,
> and only through one permissionless, reversible operation (hide an app).** See "V2 Flow Sentinel".

## The four mechanisms (pure cores)
- **`schmitt.py`** — multi-level Schmitt discretizer. Asymmetric enter/exit thresholds
  (deadband) + edge-trigger + K-poll dwell ⇒ micro-fluctuation cannot re-trigger; a level emits
  exactly once per genuine transition.
- **`rollup.py`** — watchdog activity rollup. Rising-edge `active` (once) + trailing-edge `idle`
  after quiet + long-burst heartbeat ⇒ a 4000-file burst collapses to one event.
- **`limiter.py`** — token bucket (`RATE=5/s`, `CAPACITY=10`). Hard backstop: arrival ≪ ONA's
  drain rate, so the 40-slot buffer can never approach full. Overflow is coalesced + logged.
- **`narsese.py`** — pure event builders (`<cpu --> [pegged]>. :|:`).

## Constants (initial, tunable — mechanism exact, values are dials)
CPU rising/falling `15/55/88` ↑ · `7/47/80` ↓ (Δ=8). Mem `50/75/90` ↑ · `45/70/85` ↓ (Δ=5).
`DWELL_K=2`, poll `2.0s`, `T_QUIET=1.0s`, `T_MAX=30.0s`, rate `5/s`.

## Shell
`SystemSentinel(sink, watch_dirs=[...])` — `run_once()` polls psutil + ticks rollups + flushes
overflow; `watch()` starts a watchdog observer. Requires `pip install psutil watchdog`
(lazy-imported, so the pure cores test without them). `sink` is a callback, e.g. `brain.add_belief`.

## Tests
From `src/`: `python3 -m sentinel.test_schmitt | test_rollup | test_limiter | test_narsese` (all pure).

## V2 Flow Sentinel (protect un-self-observable focus)
A SECOND, fully isolated ONA instance (separate subprocess ⇒ mathematically zero cross-contamination
with the Knowledge brain) plus an unprivileged macOS sensor. It learns your *normal* attention,
detects a fragmentation spike, and offers ONE reversible action.

- **`sensor.swift` / `sensor.py`** — unprivileged macOS telemetry: NSWorkspace push notifications for
  frontmost-app changes (reads only the app's coarse **category**, never window titles ⇒ no TCC
  dialog, no root, no polling). `sensor.py` maps bundle→bucket via Apple's `LSApplicationCategoryType`
  UTI taxonomy + a small override table, memoized in `store.py`. The Swift helper is now
  **bidirectional**: stdout = events, stdin = `hide <bundle>` commands actuated via
  `NSRunningApplication.hide()` (verified permissionless + dialog-free in an accessory `NSApplication`
  run loop; its return Bool is unreliable, so we ignore it). stdin uses `readabilityHandler` (off the
  main run loop) so it never blocks the CFRunLoop powering the NSWorkspace stream.
- **`fragmentation.py`** — dual-plane funnel: a measurement-plane ring (every micro-switch, full
  fidelity) vs an ingestion-plane Schmitt (only rate-level crossings reach ONA).
- **`surprise.py`** — `SurpriseDetector` with the **epistemic burn-in gate** (`min_confidence`): it
  may interrupt only when the baseline belief's ONA confidence ≥ **0.85**. Day-1 / low-evidence
  baselines stay silent — never cry wolf. The baseline is a binary `<attention --> [steady]>` belief
  (`intervention.steadiness_belief`): steady=freq 1, fragmenting=freq 0. Each observation carries
  **single-evidence confidence 0.5** (w=1), so confidence accumulates by NAL revision —
  `0.50, 0.67, 0.75, 0.80, 0.83, 0.857…` — and the 0.85 floor is reached only after **~6
  confirmations** (measured against real ONA; this is exactly c=w/(w+k), k=1). A high per-observation
  confidence would slam the belief to ~0.9 in one step and erase the burn-in (measured: armed at
  obs #2) — `test_surprise.test_steadiness_burn_in_is_six_confirmations` guards this.
- **`intervention.py`** — deterministic, closed-vocabulary prompt (no LLM near temporal logic):
  *"Fragmentation spike (…) — hide [comms] apps for 25m? [y/n]"*. On `y` the console actuates
  `sensor.hide(bundle)`; on the bad direction only (never on recovery), one prompt at a time.
- **`focusblock.py`** + `store.py` KPI — the value metric: median focus-block duration AFTER vs
  BEFORE accepted interventions ("minutes of focus protected"), surfaced in `health`. Pure; clock
  injected; persisted (durations + timestamps only, never app/content).
- **Calibration (privacy-preserving)** — `store.calib()` returns scalars only: empirical burn-in
  (elapsed + observations to cross the floor, recorded once by the console), and the false-positive
  proxy (interventions fired vs declined → decline rate). These tune the 0.85 floor. No raw event
  stream, category, or title ever enters this path — the only thing that leaves the machine is a
  human reading these numbers off `health` and relaying them.

Opt-in via `sentinel on` in the console. Tests (pure/deterministic, no sleep):
`test_fragmentation | test_surprise | test_intervention | test_focusblock`, plus `test_sentinel_v2`.

## Related
PRD C3 / M2; ADR-001.
