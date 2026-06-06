# sentinel

## Overview
The local observation pipeline (C3 / M2). **Observe-only** — it watches the OS and emits
discretized Narsese events; it never acts. All flooding-prevention math lives in the pure
functional core; psutil/watchdog are a thin shell. If this pipeline leaked, ONA's 40-slot
attention buffer would flood and the reasoner would collapse — so the cores are designed so
that **an idle machine emits zero events** and floods are mathematically unreachable.

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

## Related
PRD C3 / M2; ADR-001. Surprise detection (ONA prediction failure) + LLM narration are the next M2 pieces.
