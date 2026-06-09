# ADR-021: Accessibility Tree Automation Core (AX actuator)

## Status
Accepted. The first time JARVIS can drive the GUI — semantically, via the macOS Accessibility API,
consent-gated. Python suite 333 → **345** green; Swift compiles (`build.sh`). Live verification of
the brightness PoC is the human-in-the-loop step (Swift has no pytest harness, per ADR-017).

## Context
JARVIS could only run a closed catalog of fixed shell/osascript commands; it could not move a UI
control. The trigger was "set brightness to 45%" — Apple exposes **no public brightness API**, and
the standard `brightness` tool **fails on this Apple-Silicon XDR panel** (`-536870201`). The honest
fix is to act the way a user does, but *semantically*: target real **AX elements**
(`AXSlider "Brightness" → 0.45`), never pixel coordinates.

This is the project's largest capability and largest risk — it steps outside the closed-catalog
guarantee every prior safety layer relied on. Three ratified decisions contain it:
- **Closed verbs, live targets.** The action *verbs* stay a finite catalog (`ax_press`,
  `ax_set_value`); only the *targets* are live element ids from the current screen.
- **Consent-gated.** Every actuation routes through the ADR-020 consent machine (Approve/Deny).
- **TOCTOU-safe.** `AXUIElementRef` is ephemeral and never serialized; ids re-resolve to a descriptor
  at approval time and **abort on drift**.

## Decision
Split brain and body across the existing socket; only strings cross.

**Body — `JARVIS.app`** (the sole holder of the Accessibility/TCC grant):
- `AXPermission.swift` — `AXIsProcessTrusted()` + prompt/deep-link UX (handles ad-hoc-rebuild grant
  loss gracefully).
- `AXSerializer.swift` — walks the **focused window**, prunes to actionable roles
  (Button/Slider/CheckBox/TextField/PopUp/Radio/MenuItem/…), depth- and count-capped (14/50),
  assigns stable ids (`sld_1`), emits a compact text DOM (`[sld_1] AXSlider "Brightness" = 0.6`), and
  keeps the **in-process** `id → {element, descriptor}` map. One shared walk used by serialize + re-resolve.
- `AXActuator.swift` — re-resolves the descriptor against the **live** tree at actuation time
  (`AXIdentifier` → role+title → indexPath; cached ref only if it still matches), guards
  `AXIsProcessTrusted`, performs the verb (`kAXPressAction` / set `kAXValueAttribute`), and **aborts on
  drift/ambiguity** — never guesses.
- `AppDelegate` — on `NSWorkspace.didActivateApplication` (skipping itself) serializes the focused app
  and pushes `ax_context {epoch, dom, ids}`; on an `actuate` event runs `AXActuator` and replies
  `ax_result`.

**Brain — Python daemon** (never holds an element ref):
- `actions/catalog.py` — closed `kind="ax"` verbs `ax_press`, `ax_set_value` (`confirm=True`),
  **excluded from the static prompt list** (surfaced only with the live DOM). `AX_VERBS` is the set.
- `service/ax_dispatch.py` — validates verb∈catalog and id∈current epoch, then opens a consent whose
  on-approve **emits the `actuate` event**; invalid verb/id is a safe refusal that opens no consent.
  Pure of Session/socket → unit-tested.
- `jarvis.py` — an `ax_provider` injects the focused-window DOM into the converse prompt (mirrors the
  ADR-010 `context_provider`); `[[DO: ax_*]]` verbs route to `ax_dispatch`, everything else to the
  ADR-019 action runner.
- `service/session.py` — caches `{epoch, dom, ids}` from `ax_context`; serves the provider/dispatch;
  emits `actuate`; surfaces `ax_result` as an answer.

**Loop:** app serializes → `ax_context` → daemon caches + injects DOM → LLM emits `[[DO: ax_set_value:
sld_1 45]]` → daemon validates + `consent_request` → user Approves → daemon emits `actuate(epoch,
sld_1, ax_set_value, {value:45})` → app re-resolves, checks trust, sets the slider → `ax_result ✓`.

## Consequences
- **Gained:** JARVIS can now semantically operate any AX-exposed control, consent-gated, with no
  coordinate guessing and no new model — the real "act on my behalf."
- **Tests:** +12 (catalog AX verbs; `service/test_ax_dispatch.py`; converse DOM-injection + verb
  routing). All pure/stubbed — **no OS side effects**. Swift = live checklist.
- **Honest limits / deferred:**
  - **Swift has no pytest harness** — the body is verified by the live checklist; the brain is unit-covered.
  - **AX-blind apps** (Electron, custom canvases, games) expose thin/empty trees — out of scope; the
    only place a future **Vision fallback** would apply (and only with a local VLM).
  - **Ad-hoc signing** invalidates the TCC grant on rebuild — handled by detect-and-prompt; a
    developer-ID signature (stable grant) is deferred.
  - **v1 verbs are minimal** (`ax_press`, `ax_set_value`); drag, typing, multi-window/app workflows,
    and live AX-observer streaming are deferred. Serialization is on focus-change, not continuous.
  - **Brightness PoC is multi-step** — Displays settings must be the focused window first (open it,
    then ask); expected for v1.
  - **No raw mouse/CGEvent fallback** — rejected (brittle, blind, highest-risk). AX only.
- **Safety posture:** every actuation is consent-gated and re-resolved; drift/ambiguity/expiry/lost-
  permission all default to **no action**. Nothing clicks unconfirmed or against a stale snapshot.

## Alternatives Considered
- **Multimodal vision pipeline** (screenshots → coordinates): rejected — needs a second resident VLM
  on the M3 Pro, gives a brittle pixel guess, and can't be cleanly consent-gated ("click (812,437)"
  is a blind spot). AX maps perception and action to one element identity.
- **Raw CGEvent mouse/keyboard synthesis:** rejected as the primary — blind to what it clicks and
  breaks when a window moves; AX targets the element regardless of layout.
- **Run AX from the daemon:** impossible cleanly — TCC is per-executable; framework-Python is a poor
  grant target. The signed app is the right body; refs never cross the socket.
