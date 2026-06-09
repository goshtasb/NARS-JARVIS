# ADR-024: AX label enrichment (Phase 1) + bounded agent loop (Phase 2, deferred)

## Status
Phase 1 **Accepted & live-verified**: the serializer now recovers labels that macOS stores in a
separate element, which unlocked the `increase_contrast` recipe ADR-023 had to drop. Phase 2 (bounded
agent loop) designed and deferred. Suite **354** green. Daemon grant persisted across rebuilds.

## Context
ADR-023's live `axdump` proved the perception gap: many System Settings controls expose an **empty
`AXTitle`** — every Accessibility → Display checkbox came back as `[checkbox_N] AXCheckBox = 0`, no
label. Title-matching couldn't reach them, so the recipe catalog couldn't grow. **Capability is
bottlenecked by perception; fix perception first.**

## Decision — Phase 1: label enrichment (Swift, `AXSerializer.swift`)
Replace the `title = AXTitle ?? AXDescription` read with an `enrichedLabel(el)` **priority cascade**
(explicit-linkage first, spatial last — never spatial-first), stopping at the first hit:
1. own `kAXTitleAttribute` (the normal case).
2. **`kAXTitleUIElementAttribute`** → that element's value/title — the authoritative OS-level link
   SwiftUI/Catalyst set even when the control's own title is empty.
3. own `kAXDescriptionAttribute` / `kAXHelpAttribute`.
4. **structural sibling**, spatially tie-broken — nearest `AXStaticText` among the parent's children
   (via `kAXParentAttribute`), nearest by frame (`kAXPosition`/`kAXSize`).

The enriched label flows into the existing `AXDescriptor.title`, so the DOM, recipe matching
(`find_control_id`), and the actuator's TOCTOU re-resolution all benefit — no other Swift change
(`AXActuator` reuses `collect`). **No DOM bloat:** only actionable roles are emitted, so the donor
`AXStaticText` is never listed; enrichment lifts its string into the control's own line. Result on the
live pane: `[checkbox_2] AXCheckBox "Increase contrast"`, `AXSlider "Display contrast"`, "Reduce
transparency", "Color filters", etc. — all previously blank.

**Then the catalog grew by data alone** (`actions/recipes.py`): re-added `increase_contrast`
(Accessibility → Display / `AXCheckBox` "increase contrast" / `ax_press` / FRICTIONLESS), matcher
confirmed against the live dump. Live: "increase the contrast" from Chrome → opened the pane itself →
toggled the checkbox frictionlessly. The "perception unlocks capability" thesis, proven.

## Consequences
- **Gained:** untitled toggles/sliders are now addressable; the user's original "contrast" request
  works; new System Settings recipes are now genuinely just data rows (matcher verified via `axdump`).
- **Tests:** suite **354** (the generic `RECIPES`-driven tests in `test_recipes.py`/`test_catalog.py`
  auto-cover the new row). Swift enrichment is verified by the live `axdump` + actuation checklist
  (ADR-017: no Swift pytest harness).
- **Bug found & fixed mid-build (honest):** the first cut called `enrichedLabel` on *every* node,
  whose sibling/frame traversal stalled serialization tree-wide (50% CPU, empty DOM). Fix: run the
  cascade **only for actionable nodes** (≤ `maxNodes`). Confirms the value of `axdump` + the
  status-readout for diagnosing the perception layer.
- **Grant persistence re-confirmed:** two app rebuilds this ADR, DR leaf unchanged
  (`certificate leaf = H"f36c674d…"`), no re-grant needed — ADR-021 stable signing holding.
- **Honest limits:**
  - `increase_contrast` uses `ax_press`, which **toggles** (flips current state) rather than setting
    ON/OFF explicitly. A set-to-state variant (read state, press only if needed) is a small follow-on.
  - Spatial tie-break is last-resort; most controls resolved at tier 1–2 (explicit link / own attrs).
  - Per-recipe matcher still verified live before shipping — enrichment makes labels *available*, not
    *guessable*.

## Deferred — Phase 2: bounded agent loop (next)
For arbitrary, non-cataloged targets: a GATED open → re-perceive → act loop — LLM emits a navigate
intent, daemon opens + waits for the fresh (now label-enriched) `ax_context`, re-prompts the LLM for a
concrete `ax_*` verb, **consent-gated** per actuation, **bounded to ≤2 hops**, fail-safe on miss. Built
on this now-proven perception layer, as its own commit/ADR.

## Alternatives Considered
- **Spatial-first matching:** rejected — fragile to layout/RTL/multi-column; explicit `AXTitleUIElement`
  is authoritative and resolution-independent.
- **A `consumed-nodes` set to dedup donor text:** rejected as dead code — the actionable-only emit
  filter already excludes all `AXStaticText`; there is nothing to dedup.
