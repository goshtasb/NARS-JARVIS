# ADR-023: Generalized app-navigation catalog (declarative recipe table)

## Status
Accepted. The frictionless-vs-gated boundary is now a declarative data table, not branch logic or
prompt behavior. Brightness migrated into it and live-verified end-to-end. Python suite 348 → **354**
green. Daemon-only (no app rebuild → the Accessibility grant is untouched).

## Context
ADR-022 shipped self-navigation, but the one recipe (brightness) was **hard-coded** in
`session._nav_dispatch` (`if name != "set_brightness"`). Expanding to more domains that way means
per-domain branches and scatters the security boundary across `session.py`.

The deeper driver (from the ADR-022 brightness double-change bug): **friction must be deterministic
data, never an LLM verb choice.** The model proposes *intent*; the application maps intent → an
immutable FRICTIONLESS/GATED policy. Anything off-table fails safe to GATED.

## Decision
A declarative recipe catalog as the single source of truth for the boundary.

- **`actions/recipes.py` (pure):** `Recipe(intent, label, surface, role, title, verb, friction,
  takes_value)` + the closed `RECIPES` table. `recipe_for(intent)` (None = fail-safe), `nav_actions()`
  (drives the catalog/prompt), and `should_gate(recipe)` — the **one** friction decision, read straight
  from the row's `friction` field. Deep links live here, not in `session.py`.
- **`actions/catalog.py`:** the `kind="nav"` Actions are **generated from `RECIPES`** — adding a
  domain is a data row, never a code change.
- **`service/session.py`:** `_nav_dispatch` is now generic — look up the recipe, parse a value if it
  takes one, find its control (`find_control_id` on `role`+`title`), and if absent open `r.surface`
  and stash a pending request fulfilled when the surface's controls arrive (`_fulfill_pending_nav`).
  `_actuate_recipe` makes the lone friction call: `should_gate(r)` → FRICTIONLESS emits the actuate
  directly; GATED routes through the ADR-020 consent gate. The general `ax_*` verbs stay always-gated.
- **`axdump` command (new):** returns the captured control tree — the tool for authoring/verifying
  recipe matchers against what macOS actually exposes (not guessing).

**Invariant:** FRICTIONLESS ⟺ membership in `RECIPES`. Unknown intent / unmatched control / timeout →
no action (general verbs, which are gated, handle anything off-table). The LLM never decides friction.

## Consequences
- **Gained:** the friction boundary is one auditable table; new OS domains are data rows; the
  brightness path is now generic and live-verified (Chrome → "set brightness to 30%" → opened Displays
  itself → actuated frictionlessly, no consent).
- **Tests:** +6 (`actions/test_recipes.py` — schema integrity, fail-safe `recipe_for`, and `should_gate`
  proving friction-from-data via synthetic FRICTIONLESS/GATED rows incl. an `ax_press` toggle;
  `test_catalog.py` — nav Actions generated from `RECIPES`). All pure. Suite **354**.
- **Key empirical finding (honest scope):** a live AX dump showed **many System Settings controls
  expose an EMPTY `AXTitle`** — their label lives in a *sibling* element (e.g. every Accessibility →
  Display checkbox, including "Increase contrast"). Title-matching only reaches controls macOS labels
  directly (like the Brightness slider). So v1 ships **one live recipe** (brightness); the planned
  `increase_contrast` toggle was **dropped rather than shipped broken**. The schema already supports
  `ax_press` toggles and GATED friction — only live label availability is the gating factor.
- **Deferred → ADR-024:**
  - **Serializer label-enrichment** — associate sibling `AXStaticText` labels with their controls, so
    untitled toggles (contrast, True Tone, Night Shift, most Sound/Wi-Fi/Bluetooth switches) become
    addressable. This is the real unlock for broad recipe coverage.
  - **The bounded open → re-perceive → act agentic loop** for arbitrary, non-cataloged targets (entirely
    in the GATED lane).
- **Known edge:** if a target app is *already frontmost* on a different pane, switching panes via deep
  link fires no activation, so no re-serialization — the recipe times out. From another app (the normal
  flow) it works. A post-pane-change re-read would close this (with label-enrichment, ADR-024).

## Alternatives Considered
- **Prompt-level friction (status quo):** rejected — the brightness double-change bug proved a
  probabilistic model can't govern a security boundary.
- **`if domain == …` branches in `session.py`:** rejected — doesn't scale, fractures the boundary.
- **Ship `increase_contrast` anyway:** rejected — its AX label isn't exposed; it would silently
  fail-safe to "couldn't find," which reads as broken. Honest scope: one working recipe + the finding.
