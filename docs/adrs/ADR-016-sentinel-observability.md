# ADR-016: Sentinel observability & dry-run

## Status
Accepted — the prerequisite instrumentation for exercising the live Flow-Sentinel autonomy loop. No
real actuation is armed here; that remains a separate, later, deliberate step.

## Context
The autonomy loop (Swift sensor → `SurpriseDetector` → 0.85 NARS gate → `Sensor.hide`) had only run
under mocks. Two gaps blocked a safe live run: (1) the detector computed `surprise`, `prior_exp`,
`actual_exp`, `prior_conf` every observation and **discarded** them — we could see threshold
*crossings* (burn-in alert, acted/intervention) and post-hoc KPI, but never the *approach*; and (2)
there was no way to exercise the full chain without a real cross-app hide.

## Decision
**Per-observation trace (behind `NARS_JARVIS_TRACE`).** `SurpriseDetector.observe` now records
`last_surprise/last_prior_expectation/last_actual_expectation` (alongside `last_prior_confidence`) —
non-breaking, just surfacing already-computed numbers. `SentinelLoop._handle` logs one
`service.sentinel_trace.format_observation(...)` line per tick to the daemon log (stderr →
`nars-jarvisd.log`), **numeric + coarse-category only — never an app id/title/content**.

**Gate-proximity in `sentinel status`.** `format_gate_proximity` shows, per distraction category, the
live `expectation` vs the `0.85` arm floor (`comms E=0.62 Δ0.23-to-arm` / `… ARMED`) — turning the
black-box gate into a debuggable readout. Always on (cheap).

**Dry-run (behind `NARS_JARVIS_DRY_RUN`) — full brain, disconnected hands.** It keeps the *entire*
state machine live (intervention prompts, consent, `_feed_consent` belief updates, KPI, undo/ratchet)
so the gate can still train to passing and the auto-hide *decision* is observable — and suppresses
**only the physical actuation**, at two layers: `SentinelLoop._on_surprise` skips the `sensor.hide`
loop and emits `[dry-run] WOULD hide {cats} (gate passed)`, and `Sensor.hide/unhide` is a hard
backstop that refuses to `_send` under dry-run (so even a missed call site cannot actuate). Pure-passive
was rejected: with the consent UI suppressed the gate would never reach `0.85`, so the auto-hide branch
could never be observed.

Both flags default OFF → zero change to normal operation. Set them before `sentinel on`.

## Consequences
- **Gained:** a real-time time-series of the expectation math approaching the gate; a per-category
  proximity readout; and a safe way to run the complete sensor→NARS→threshold→consent→gate→decision
  pipeline live with the actuator physically inert.
- **Privacy preserved:** trace/status carry only numbers + coarse buckets (asserted in tests).
- **Unlocks** the live integration run: `NARS_JARVIS_TRACE=1 NARS_JARVIS_DRY_RUN=1` → `sentinel on` →
  switch apps → watch the trace climb and a `WOULD hide` decision fire with nothing hidden. Arming real
  actuation is a future deliberate step.
- **Limits (honest):** flags are env-set at `sentinel on` (no live runtime toggle command yet); the
  trace lands in the daemon log (no UI stream yet); the live loop still requires the macOS sensor
  (`swiftc` + sensor.swift) to build/run, so it can only be exercised on a capable host.

## Alternatives Considered
- **Pure-passive dry-run (suppress all UI):** rejected — the gate can't train, so the auto-hide
  decision is never reachable; only the detector math would be observable.
- **Change `observe()`'s return signature** to emit the values: rejected — would churn callers/tests;
  recording `last_*` attributes is non-breaking.
- **Stream the trace as client events:** deferred — would spam the UI; the daemon log is the right
  sink for a debug time-series.
