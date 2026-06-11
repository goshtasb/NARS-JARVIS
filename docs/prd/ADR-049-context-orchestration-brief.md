# Product Brief — ADR-049: Context Orchestration Layer

> **Status:** Ratified Phase-2 product brief (Phase-1 boundaries locked). **Design only — build deferred
> to after the 2026-06-16 field-test review.** No code is written from this brief during the bake window.
>
> **ADR numbering (corrected):** this layer = **ADR-049**. The Passive Behavioral-Learning Loop it
> unblocks = **ADR-050** (ADR-048 is already shipped — the Sentinel auto-start, v1.14.9). The brief text
> below originally said "ADR-048" for the observer; read it as **ADR-050**.

## 1. Executive Summary
NARS-JARVIS envisioned a proactive assistant that learns behavior and orchestrates the environment, but
is constrained by an **actuation gap**: a local, privacy-first reasoning engine with no tools to
meaningfully manipulate the macOS workspace. ADR-049 introduces the Context Orchestration Layer — a
tiered execution backend to manage system states, launch/deep-link applications, and control Focus
modes. It is the **prerequisite to the Passive Behavioral-Learning Loop (ADR-050)**: engineer the
actuators before deriving value from the observer.

## 2. Problem Statement
JARVIS today is read-heavy (diagnostics, web research, reactive queries) and cannot alter the user's
digital context. Entering "Deep Work" can't pause notifications, change appearance, or set Focus. So any
passively-observed routine would yield **empty propositions** — offering tasks the user can trivially do
themselves. This actuation poverty makes the "proactive assistant" a glorified launcher.

## 3. Solution & Vision
A privacy-safe Context Orchestration Layer that proposes and executes **multi-step routines**, collapsing
complex digital transitions (start a coding session, wrap up the workday) into a **single consent-gated
confirmation**, routed through deterministic macOS APIs/CLIs.

## 4. Value Proposition & Differentiation
Unlike cloud agents needing permissive desktop access, JARVIS orchestrates **entirely locally** inside a
verifiable consent loop: every routine is explicitly consented to, **pre-flighted for TCC**, and
**deterministically verified** after execution. Zero hallucinated shell executions.

## 5. Target Audience
macOS power users / developers / privacy-centric professionals with heavy context-switching who want
frictionless transitions but refuse to grant a cloud LLM arbitrary AppleScript control.

## 6. Core User Journey — the Orchestration Loop
`propose → single consent for the whole sequence → pre-flight TCC → execute zero-TCC actions immediately,
route System Events safely, invoke the namespaced Shortcuts bridge for Focus → read back state to verify →
log the completed routine in the Cognitive Identity ledger.`

## 7. Scope (V1) — the tiered backend
- **Tier 1 (zero-TCC, primary):** direct CLIs — hardware/system state (`set volume`, `pmset`) and
  **application deep-linking via `open` URL schemes**. No prompts.
- **Tier 2 (one-grant):** AppleEvents **strictly to `System Events`** (e.g., Dark Mode) — a single
  Automation grant unlocks the whole system-UI class.
- **Tier 3 (exception):** the **Shortcuts bridge** (`/usr/bin/shortcuts`) **solely for macOS Focus**,
  via a one-time guided install of a single `JARVIS:`-namespaced shortcut; closed-loop via `--output-path`.

**Out of scope V1:** arbitrary Window Management via Accessibility (non-deterministic, fragile across
multi-monitor) — *deferred*; **per-application AppleEvents — banned** (app control uses `open`, not
`tell application X`, to avoid a per-app TCC prompt cascade).

## 8. Success Metrics & KPIs — the go-gate for ADR-050 (Passive Observer)
Over a two-week real-use window, all four must hold before the observer is turned on:
- **Reliability:** verified-actuation rate **≥ 95%** (read-back computationally confirms the state changed).
- **Value:** consent-acceptance rate **≥ 75%** (proposed routines are relevant, not noise).
- **Richness:** **≥ 5** distinct routine-relevant actions with real-world usage.
- **Safety:** **exactly 0** unreversed misfires requiring manual undo.

Instrumented via the consent ledger + the verify-loop, surfaced in `health` (reusing the Sentinel's
existing KPI/calibration precedent). Thresholds are tunable dials.

## 9. Assumptions, Risks, Dependencies
- **Assumption:** `shortcuts run --output-path` remains available for closed-loop verification.
- **Risk:** macOS upgrades changing TCC/System-Events authorization behavior.
- **Dependency:** the user completes the one-time guided install of the single `JARVIS:` Focus shortcut.
  If absent, Focus **degrades gracefully** (no crash) — the rest of the loop is unaffected.

## 10. Preliminary Technical Considerations
- The pipeline **never fires blindly**: any System-Events AppleScript pre-flights via
  `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded: false)`. On `denied`/`notDetermined`, halt
  the routine and route the user through the standard Consent UX to grant Automation (mirrors the
  existing `AXActuator.swift` Accessibility-grant pattern).
- The L1 model is **never** exposed to the user's Shortcuts library: the catalog statically maps internal
  intents to the rigid `JARVIS:` folder namespace — the LLM picks a closed catalog action, code disposes
  to the bound shortcut. Hallucination surface neutralized.

---
*Phase 1 (boundary defense) cleared across vectors 1–5: data scope (content-blind, NSWorkspace), JtBD
(one-tap routine orchestration), NARS pipeline (quantized threshold-crossings only), TCC containment
(three guided one-time grants), and the measured go-gate above. Build executes post-2026-06-16.*
