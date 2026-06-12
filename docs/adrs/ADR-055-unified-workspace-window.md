# ADR-055: Unified workspace window (Chat · Canvas · Cognitive Identity)

## Status
**Accepted — Phase 1 shipped** (v1.20.0). The three scattered menu-bar surfaces are now one tabbed
`NSWindow`. **Phase 2 (the Universal Composer: `+`/`/` buttons in the Chat input, a custom
field-anchored `/` overlay, and stripping the redundant Canvas palette) is NOT in this ADR** — it's the
next, decoupled step. Visual lifecycle confirmation is the user's (AppKit can't be driven headlessly).

## Context
The UI had grown into **three disconnected surfaces in three presentation modes** hung off the menu-bar
icon: Chat (transient popover, 420×320), Cognitive Identity / Habits (transient popover, 440×420), and
the Canvas (a real window, 860×620). They never coexisted, the popovers were cramped, and there was no
cohesive "workspace." The user's reaction — *"what changed?!"* — was correct: the Canvas still looked
like a mechanical composer because nothing unified the surfaces.

Separately, the v1.19.0 `/` command was **broken**: it used macOS's native field-editor completion
(`complete()`), which tokenizes on word boundaries and treats `/` as punctuation that *ends* a word —
so the `/`-prefixed token was never recognized. The native completion API is built for spellcheck, not
command routing. That fix belongs to Phase 2 (a custom overlay), not here.

## Decision (Phase 1 — container only, zero logic mutation)
One `NSWindow` whose `contentViewController` is an `NSTabViewController`
([`MainTabViewController`](../../src/ui/MainTabViewController.swift)) hosting the three **existing**
view controllers as tabs, dropped in **unchanged**:

| Tab | View controller | Role |
|---|---|---|
| **Chat** (default) | `ChatViewController` | conversational agent (+ future Universal Composer) |
| **Canvas** | `UnifiedCanvasViewController` | async task board: queue, state machine, recovery |
| **Cognitive Identity** | `HabitsViewController` | habits + the passive-observation Mirror |

- **`.toolbar` tab style** (native System-Settings look, SF Symbol per tab). The controller resizes the
  window to the selected pane's `preferredContentSize`, so a smaller pane isn't stranded in a corner.
- **The window lifecycle (the "sticky-note trap"), defined explicitly:**
  - **Normal level** (not floating) — it does not hover over other apps' windows.
  - **Does not hide on deactivate** (NSWindow's default for titled windows) — click another app and the
    workspace stays put. A *workspace*, not a popover.
  - **Close hides** (`isReleasedWhenClosed = false`); the menu bar reopens it.
  - **Menu-bar left-click toggles**: hidden → show+focus (land in the Chat input); already key → hide;
    visible-but-not-key → focus.
- **Tech-debt eradication:** both `NSPopover`s and the standalone canvas window are gone —
  `popover`, `habitsPopover`, `canvasWindow`, `openPopover`, `openHabits`, and the old `openCanvas` are
  deleted from the AppDelegate (no orphaned popover managers; stale comments updated).
- **Self-sufficient panes:** Habits gained a `viewDidAppear → refresh()` (the old `openHabits` used to
  trigger it); Canvas already self-refreshed. No other pane logic was touched.
- **UX-bridge alignment:** a fired job must **not** force-switch tabs (the ratified "notify, don't
  navigate" rule), so `chat.onOpenCanvas` is intentionally left unwired this phase — the in-chat live
  status chip is Phase 2.

## Validation
Swift compiles clean (0 warnings). A headless probe assembles the `MainTabViewController` with all three
real panes and forces `loadView()` on the container + each pane — **constructs without crashing**
(`tabs=3`, the toolbar-style auto-resize fit the window to the first pane). The *visual* lifecycle —
toggle feel, tab switching, per-pane resize, that it reads as a native workspace — is the user's to
confirm at the screen.

## Consequences
- One cohesive window; Chat and Cognitive Identity finally get real estate (was cramped popovers).
- The logical flow is now spatial: **Command (Chat) → Execute (Canvas) → Observe (Identity)**.
- Phase 2 builds the Universal Composer on this shell: `+`/`/` in the Chat input, a **custom** `/`
  overlay (replacing the broken native completion), and removal of the now-redundant Canvas palette.
