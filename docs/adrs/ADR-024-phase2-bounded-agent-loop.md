# ADR-024 Phase 2: Bounded agent loop (navigate → re-perceive → act)

## Status
Accepted & live-verified. JARVIS can now fulfill an arbitrary, non-recipe request in one utterance by
opening the surface itself, re-perceiving it, and proposing the action — gated by a single consent.
Python suite 354 → **361** green. Daemon-only (reuses the ADR-021 `ax_context`/`actuate` plane).

## Context
Recipes (ADR-022/023) cover *curated* targets frictionlessly; ADR-024 Phase 1 made controls
*perceivable*. The frontier was **arbitrary** targets not on screen and not in the recipe table. The
agent must navigate, re-perceive, and act autonomously — without a second user utterance.

## Decision — a session-owned continuation state machine
Ratified shape: **navigation is autonomous (benign, reversible); actuation is the single human
checkpoint (GATED consent).** Never block the loop; bound the chain.

- **`navigate` verb** (`actions/catalog.py`, kind `"agent"`) — the model proposes it when the needed
  control isn't on screen. Prompt hardened: *use ax_* only with an id from the on-screen list; if the
  control isn't listed, `navigate` first — never guess an id.*
- **`service/agent_loop.py` (pure):** `resolve_surface(target)` maps a target to a **vetted Settings
  deep link** (safe-open; unknown → None → refused) and `agent_route(actions)` decides the next step
  (`act` / `navigate` / `giveup`), preferring actuation.
- **First hop** (`jarvis._run_actions(actions, question)` → injected `session._navigate`): resolve to a
  safe open, stamp `_agent = {request, hop, steps, deadline, pending/driven epoch}`, `open` it.
- **Re-perceive + hidden re-prompt** (`jarvis.agent_step(request)`): a focused `AGENT_STEP_PROMPT`
  shows the model only the goal + the current label-enriched DOM and parses its single directive
  (generate+parse only — the daemon routes).
- **Drive from `tick()`** (`session._drive_agent`): once the opened pane's DOM has settled, run **one**
  `agent_step` per distinct epoch → `act` (→ **`_ax_dispatch_verb`, the GATED path** → clear) /
  `navigate` (hop++) / `giveup` (wait). Driving from tick (not every `ax_context`) lets the re-reads
  finish and bounds LLM calls.
- **Circuit breakers:** `_MAX_HOPS=2`, `_MAX_STEPS=3`, `_AGENT_TTL=12s` (tick reaps a stall). The only
  actuation path is the consent gate — autonomous *looking*, consented *clicking*. Unknown target / no
  actionable directive / timeout → no action.

## Live verification
"turn on reduce transparency" from Chrome (control not on screen):
```
→ "Reducing transparency. Opening Accessibility…"        (autonomous navigate, no consent)
→ [re-perceived the pane] consent_request: click checkbox_3   (= "Reduce transparency")
→ "⏳ Awaiting your approval: click checkbox_3"            (GATED — one human checkpoint)
```
The model chose `navigate`, then the **correct** toggle; the loop landed at exactly one consent gate.
**Fail-safe also verified:** before the prompt hardening, the model hallucinated an off-screen
`ax_press` id — `dispatch_ax` rejected it ("isn't on the screen"), **no wrong action**. Suite **361**.

## Consequences
- **Gained:** open-ended, single-utterance GUI control for arbitrary targets, safe by construction.
- **Tests:** +7 (`service/test_agent_loop.py` — `resolve_surface` fail-safe, `agent_route`
  act/navigate/giveup; `test_converse.py` — `navigate` routing, `agent_step` parse). Pure/stubbed.
- **Honest limits:**
  - **7B reliability is the real ceiling.** It needed a hardened prompt to reliably emit `navigate`
    instead of guessing an id; it's still probabilistic on multi-step. The bounds + fail-safe contain
    it — worst case "I can't," never a wrong click (actuation is gated regardless). A larger/
    instruction-tuned model would do better; the architecture is ready for it.
  - **"Do Not Disturb" is a poor target** — in Focus settings it's a navigation *button*, not an on/off
    toggle (the real toggle is in Control Center, not a Settings deep link). Demo proven on "reduce
    transparency" instead; the loop is target-agnostic. DND-via-Control-Center is a later navigation.
  - **The session state machine is live-verified** (the pure routing is unit-tested); like other
    daemon orchestration, its real test is the live run.
  - **The re-prompt blocks `tick` ~seconds** (an LLM generation) during an active loop — consistent
    with `ask`; worker-thread offload deferred. ~2s tick latency per step is acceptable for v1.
  - **Set-to-state idempotent toggle** (read `AXValue`, press only if differing) is a small later
    refinement — `ax_press` currently flips state.

## Alternatives Considered
- **Force user re-confirmation at the navigation step:** rejected — opening a pane is benign; the
  checkpoint belongs at actuation. One approval, one utterance.
- **Drive on every `ax_context`:** rejected — progressive render would thrash the LLM and risk
  premature give-ups; tick-driven on the settled DOM is one bounded step per surface.
- **Unbounded navigation:** rejected — `_MAX_HOPS`/`_MAX_STEPS`/TTL mathematically prevent wandering.
