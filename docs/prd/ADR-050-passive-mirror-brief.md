# Product Brief — Passive Observation Mirror & Validation Experiment (v1.16.1)

> **Status:** Ratified & deployed (ADR-050, tags v1.16.0/v1.16.1; deterministic prune in v1.16.2).
> Implementation ADR: [`../adrs/ADR-050-passive-observation-mirror.md`](../adrs/ADR-050-passive-observation-mirror.md).
> This brief documents the shipped feature **and** the falsifiable validation experiment that gates
> further investment.

## Executive Summary
Addresses a critical dissonance: an intelligent agent that *feels dead* because it is blind to the
user's unprompted reality. By decoupling passive observation from the Context Orchestration Layer
(ADR-049), we deployed a lightweight, durable mechanism that logs and aggregates application switches.
This brief defines the deployed architecture **and** the strict, quantitative validation experiment
required before further investment in observation or orchestration is permitted.

## Problem Statement
Before v1.16.1, JARVIS discarded all passive app-focus data unless it tripped an attention-fragmentation
alert. The Cognitive Identity UI stayed empty; the system accumulated no baseline of the user's daily
digital rhythms. An assistant that requires explicit commands to learn anything is a reactive tool, not
a proactive cognitive partner.

## Solution & Vision
A content-blind, TCC-safe observation mirror. The Sentinel stream (app identity, category, timestamp) is
persisted into `jarvis.db`, aggregated for dwell time and daily rhythms, and surfaced as a "What I've
noticed" view in the Cognitive Identity UI. It provides immediate intrinsic value (self-knowledge) and is
the foundational pipeline for future NARS reasoning and orchestration triggers.

## Value Proposition & Differentiation
Uncompromising privacy: it tracks only `NSWorkspace` bundle transitions and **discards window titles,
document contents, and keystrokes** — the value of a time-tracking suite, inside a local AI agent, with
zero cloud telemetry and zero screen-recording/accessibility prompts.

## Target Audience
A technical power user across intensive apps (IDEs, comms hubs, browsers) who requires absolute privacy
and values accurate, aggregated insight into their own context-switching.

## Core User Journey
Passive collection → active reflection. The Sentinel logs app-focus transitions without blocking the
main thread; opening the menu-bar Cognitive Identity panel runs a sub-15ms O(n) query rendering a
narrative of dominant apps, categorical splits, and peak hours.

## Scope
**In:** persistence, aggregation, UI rendering of app bundle + category + timestamp; a hard **30-day
rolling window with deterministic pruning (daemon boot + hourly)**. **Out (permanently):** arbitrary UI
visualizations (no D3/charting), any NARS attention-buffer integration, any automated
execution/orchestration on this data, and **any scraping of URLs or window titles** (preserves the
zero-TCC architecture).

## Success Metrics & KPIs — the falsifiable experiment
**Data-sufficiency gate (opens the evaluation):** ≥2 distinct working days **AND** ≥6 distinct named
apps **AND** ≥200 logged switches. (Exit on data, not calendar.)

On sufficiency, four criteria:
1. **Specificity:** > **80%** of attributed dwell-time maps to *named* app bundles (not the generic
   fallback). — automated query.
2. **Accuracy:** user confirms ≥ **3 of 4** surfaced insights (top-3 apps + busiest hour) match reality.
3. **Stability:** **0** daemon crashes; UI pull < **50ms**; table within its retention bound.
4. **Novel insight:** ≥ **1** summary line gives the user an insight they hadn't consciously realized.

**Decision logic:** 1–3 pass → technically sound. **4 passes** → observation has standalone value →
later observation layers justified. **4 fails (accurate but hollow)** → halt observation investment,
**pivot immediately to Context Orchestration (ADR-049)**. Any of 1–3 fail → **one** corrective
iteration (below), re-test once, then proceed regardless.

## Assumptions, Risks, Dependencies
- **Assumption:** bundle-level granularity yields novel insight without document-level context.
- **Risk:** the "Subjectivity Trap" — factually accurate data that fails to deliver *actionable*
  self-knowledge (criterion 4).
- **Dependencies:** the macOS `NSWorkspace` event stream; the durability of local SQLite across daemon
  restarts and memory pressure.

## Technical Considerations
- `idx_usage_time` index → **24,000 rows (30 days, power user) query+aggregate in 14.2ms** (measured:
  7-day read 3.9ms + aggregate 10.3ms). The "choked menu-bar query" risk is empirically false.
- Pruning is **deterministic** (boot via first-write + hourly elapsed-time), not probabilistic.
- **Cut-loss rule:** a corrective iteration is **≤ 1 day of pure-function fixes** (expand the bundle
  map; refine dwell math) — **no architectural expansion**. After one iteration, move to ADR-049
  regardless.
