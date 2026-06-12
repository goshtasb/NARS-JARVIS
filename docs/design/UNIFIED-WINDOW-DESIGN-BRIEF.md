# NARS-JARVIS — Unified Window Design Brief (for the designer)

> **What we need:** a complete visual + interaction design for **one standalone desktop application
> window** for a local-first macOS assistant. This is a real, resizable app window — **not** a menu-bar
> dropdown, popover, or HUD. The current build crams the functionality into three cramped menu-bar
> popovers; we are replacing all of that with this one window.

---

## 1. Product context (so you design for the right thing)

NARS-JARVIS is a **local-first, privacy-first AI assistant for macOS**. Everything runs on the user's
machine — no cloud. It has two "brains": a symbolic reasoner and a local language model. The user can:
- **chat** with it (ask questions, give instructions in plain English),
- **run jobs** (summarize a document, read a web article, run a system report) **now or scheduled**,
- watch those jobs execute on a **task board**,
- see what the assistant has **passively learned** about how they use their computer.

**Tone of the product:** calm, trustworthy, technical-but-human, honest. It never pretends. It tells the
user the truth (e.g., "this runs at 11 PM *if your Mac is awake*"). The design should feel like a
**native macOS workspace tool** (think Things, Linear's mac app, Xcode panels, System Settings) — not a
flashy consumer chat app, not a web app in a wrapper.

---

## 2. Platform, technical & brand constraints (hard requirements)

- **Native macOS, AppKit.** Use native macOS controls, conventions, and metaphors. Designs must map to
  AppKit components (NSWindow, NSToolbar, NSTableView/stack views, NSTextField, NSProgressIndicator,
  NSMenu, NSPopover for transient pickers). No iOS patterns, no web-only patterns.
- **Light AND Dark mode**, both first-class. Respect the system accent color where appropriate.
- **SF Symbols** for iconography (native, themable, accessible). Provide symbol names where you choose
  specific icons.
- **Dynamic Type / accessibility:** must support larger text sizes, VoiceOver labels, full keyboard
  navigation, and WCAG AA contrast in both modes.
- **Privacy invariant (critical, non-negotiable):** the assistant is **content-blind**. It senses only
  *which app* is in front (by name) — **never window titles, document contents, URLs, or keystrokes.**
  The design must **never imply** we see screen contents. The "what I've noticed" view shows app names
  and time only.
- **Minimum window size must remain usable.** Target macOS 13+.
- **No telemetry/marketing surfaces.** This is a personal tool.

---

## 3. The window shell (global chrome)

A single standalone `NSWindow`.

| Property | Spec |
|---|---|
| Type | Standard titled, **resizable** window. Real window — appears in the window list, can be minimized, full-screened. |
| Default size | ~960 × 680 (design for this; must reflow gracefully). |
| Minimum size | ~720 × 520 (define a usable minimum; nothing should clip). |
| Title | "JARVIS" |
| Tab switcher | Lives in the **window toolbar** (System-Settings style: icon + label per tab). Design the toolbar tabs. |
| Level / behavior | **Normal** window level (does NOT float above other apps). Does **not** hide when the app loses focus — it's a workspace that stays put. |
| Close | Hides the window (does not quit the app); reopened from the menu-bar icon. |
| Launch | Summoned from a **menu-bar status icon** (a small "JARVIS" item). Design (a) the menu-bar icon in connected/disconnected states, and (b) decide if the app should also show a **Dock icon** when the window is open (recommend yes for a "real window" feel — call this out). |
| Connection status | The app can be **connected or disconnected** from its background engine. Design a clear, quiet status indicator (menu-bar icon state + an in-window indicator). Disconnected = a reconnecting state, not an error scream. |
| Global emergency control | A persistent, unmistakable **"Stop everything"** affordance (kills the whole assistant). Must be reachable but not accidentally hit. |

**Information architecture — three primary tabs (in this order):**
1. **Chat** (default landing tab) — talk + compose jobs.
2. **Canvas** — the task board where jobs execute.
3. **Cognitive Identity** — what the assistant has learned (the "mirror").

The spatial story is **Command → Execute → Observe**, left to right.

---

## 4. TAB 1 — Chat (the Universal Composer)

The primary surface. It is both a **conversation** and the **command bar** for creating jobs.

### 4.1 Layout zones
- **Transcript / log** (scrollable, fills most of the tab): the running conversation + system messages.
- **Input bar** (pinned bottom): a multi-affordance composer.
- **Inline consent bar** (appears above the input only when needed).
- **Live task chip(s)** (appear in the log when a job is running — see 4.6).

### 4.2 The input bar — design every affordance
A single-line (auto-growing acceptable) text input with these adjacent controls:

| Control | Behavior | Notes |
|---|---|---|
| **Text field** | Type a question OR a job in plain English. | Placeholder e.g. "Ask, or type / to run a job…" |
| **`+` (attach)** | Opens the native file picker; the chosen file becomes the job's target. | Show the attached file as a removable **chip/token** in or above the input. |
| **`/` (action picker)** | Opens a **searchable overlay list** of the assistant's available actions (verbs). | Full spec in 4.3. This is the marquee interaction. |
| **🎙 Voice** | Push-to-talk. Toggles: idle ("Listen") ↔ recording ("Stop & send"). Auto-stops at 30s. | Design idle + recording states (recording should be obvious — e.g., red, pulsing). |
| **Send** | Submits. ⏎ also submits. | |

Design the **empty / focused / typing / disabled (disconnected)** states of the whole bar.

### 4.3 The `/` action-picker overlay (design this in detail)
When the user types `/` (or clicks the `/` button), a **floating, searchable list** appears anchored to
the input field (a dropdown above/below the field — NOT a giant modal). It mirrors the modern pattern in
tools like Linear / Slack / the Cursor command menu.

- **Content:** the assistant's catalog of actions ("verbs"), e.g.: *summarize a document, read a web
  article, read a file, web search, system report, audio status, network status, largest apps, find a
  file, open an app, open a URL, set volume, mute, empty trash …* (10–25 items, can grow).
- **Each row:** an icon, the action's friendly name, a short description, and a **tag** indicating
  whether it is **Autonomous** (runs on its own — safe/read-only) or **Held** (needs the user's
  approval — can change the system). These two tags need distinct, calm visual treatments (e.g., a green
  "Auto" pill vs an amber "Needs approval" pill). **Do not use alarming red for "Held."**
- **Search/filter:** as the user types after `/`, the list filters live (fuzzy). Show a "no matches"
  state.
- **Keyboard:** ↑/↓ to move, ⏎ to choose, Esc to dismiss. Design the **focused row** state.
- **After choosing a verb:** the verb is "pinned" into the input; the user continues typing the
  **target and timing in plain English** on the same line, e.g. `/summarize a document  the PRD on my
  desktop tonight`. Design how the pinned verb looks (a token/chip) vs the free-text remainder.
- **Positioning:** must not clip off-screen; opens in the direction with room.

### 4.4 The `+` file attachment
- Opens the native open-panel.
- The selected file shows as a **removable chip** (filename + ✕). Design chip + its remove control.
- Multiple attachments are out of scope for v1 (one target per job) — but design the chip so it could
  stack later.

### 4.5 Message / log row types (design each)
The transcript contains several message kinds — give each a clear, distinct, scannable style:
1. **User message** (what the user typed/said).
2. **Assistant answer** (plain text reply).
3. **Transcript of voice** (what was heard, e.g. prefixed with a mic glyph).
4. **Saved/confirmation** (e.g. "✓ saved: …" when the assistant learns something).
5. **System alert / sentinel notice** (the assistant noticed something about the machine).
6. **Error / clarification** (e.g. "I couldn't match that — can you rephrase?").
7. **Job acknowledgment** → becomes the **live chip** (4.6).

These should be visually differentiable at a glance (alignment, color, icon, weight) without being noisy.

### 4.6 Live task status chip (important UX rule)
When the user fires a job from Chat, **we do NOT switch them to the Canvas tab** (no hijacking their
flow). Instead an **inline status chip** appears in the log and **updates live**:
`▶ Summarize (report.pdf) — working ▓▓▓░░ chunk 3/12` → `✅ done` / `❌ failed`.
- Design the chip's states: **queued, running (indeterminate), working (determinate progress with
  "chunk i/N"), done, failed, scheduled** (e.g. "🗓 scheduled for 11 PM").
- The chip has a **"View on Canvas"** affordance (user-initiated jump only).
- The **Canvas tab itself shows an active-task count badge** (ambient awareness) — design that badge.

### 4.7 Inline consent bar
When the assistant wants to do something that needs permission, a bar appears: a short prompt +
**Approve** / **Deny** buttons (Approve is the affirmative/green; Deny is the safe default and should
read as safe, not destructive). It self-dismisses on a timeout. Design the bar + its appearance/dismiss.

### 4.8 States to design for Chat
Empty (first run, nothing said yet) · active conversation · job running (with chip) · consent pending ·
disconnected (input disabled, reconnecting) · voice recording.

---

## 5. TAB 2 — Canvas (the task board / execution surface)

Where jobs live, run, and recover. **Three sub-tabs** (a segmented control at the top of this tab):
**Now · Scheduled · Activity.** All three render the **same task-row component** filtered by state.

### 5.1 The Task Row — the single most important component
Design **one flexible row** that expresses all of these states (driven by real backend data):

| State | Meaning | Suggested glyph / color (refine these) |
|---|---|---|
| **QUEUED** | waiting to start | ⏳ grey / tertiary |
| **RUNNING** | started, no progress detail yet | ▶ blue, indeterminate spinner |
| **WORKING** | running with measurable progress | **determinate progress bar** + "chunk i / N" label, blue |
| **DONE** | finished successfully | ✅ green |
| **FAILED** | finished with an error | ❌ red |
| **HELD** | needs the user's approval before running | ⏸ amber |

A row shows: the **action name + target** (e.g. "Summarize — report.pdf"), the **state badge**, and a
**body that depends on state**:
- DONE → a **selectable, copyable result panel** (e.g. the document summary text). Could be long;
  design scroll/expand/collapse for long results.
- FAILED → the **error message** (selectable) + the **recovery controls** (5.2).
- WORKING → the determinate progress bar + "chunk 3/12".
- HELD → "needs approval" + Approve/Deny.
- SCHEDULED (a queued row with a future time) → a **countdown** ("in 6 h 12 m") and the scheduled time.

Design **hover, selected, and expanded** states of the row.

### 5.2 Failed-row recovery (design all three affordances)
A failed job is never a dead end. Beneath the error, the row offers:
1. **↻ Retry** — run the same thing again (for transient failures).
2. **Change tool ▾** — a dropdown of **alternative actions** that accept the same target (e.g. a file
   sent to the web-reader by mistake offers "Summarize a document" / "Read a file"). Design the dropdown
   + its empty state ("no alternative for this input").
3. **Editable target + Re-run** — an **inline editable field** pre-filled with the path/URL (for typos
   or a hallucinated path) + a "Re-run" button. This is essential: it lets the user fix a wrong path
   without re-doing the whole request.

### 5.3 Sub-tab: Now
Live view of currently running / queued-immediate jobs. Watch the state machine animate. **Empty state:**
"Nothing running."

### 5.4 Sub-tab: Scheduled
Upcoming jobs that run later. Includes:
- A list of upcoming jobs with **relative countdowns**.
- **Quick scheduling presets** when composing: **Tonight 2 AM · In 1 hour · In 4 hours** (design these as
  a control set). (A full calendar/date picker is intentionally **out of scope** for v1.)
- **The honest sleep disclaimer — must be visible, not hidden in a tooltip:** *"Runs at the chosen time
  if your Mac is awake — otherwise on the next wake. Nothing runs while the Mac is off."* Design where
  and how this truthful constraint is shown (prominent but calm).
- **Empty state:** "Nothing scheduled."

### 5.5 Sub-tab: Activity
Finished work + approvals:
- **Completed/failed results** (the DONE/FAILED rows with their result/error + recovery).
- **Held for approval** section: jobs the assistant refused to run unattended, each with **Approve /
  Deny** (approving runs it now).
- A **"Clear completed"** action to flush finished rows.
- **Empty state:** "No finished tasks yet."

### 5.6 Active-task badge
The Canvas tab (in the window toolbar) shows a **count badge** when jobs are active. Design it.

> **Note for the designer:** the old Canvas had a manual "drag/click-to-build action palette." We are
> **removing it** — composing now happens by language in Chat. Do not design a palette. The Canvas is a
> **monitor / management** surface, not a composer.

---

## 6. TAB 3 — Cognitive Identity (the passive mirror + learned habits)

What the assistant has learned about the user — calm, reflective, **privacy-forward**.

### 6.1 "What I've noticed about your computer use" (the Mirror)
A passive, read-only summary built only from **which app was in front and for how long** — never
contents. Example real output to design around:
```
What I've noticed about your computer use (3 hours, 108 app switches):
- Most of your time: Cursor (2 h 10 m), Chrome (1 h 5 m), Terminal (8 m)
- By kind: development, communication, media
- Busiest around: 3 PM
(Learned passively from which app is in front — never your screen contents.)
```
Design this as a **scannable, friendly card** — possibly with a simple bar/breakdown for "most of your
time" and "by kind." **The privacy line must always be present.** Also design a **data-gathering /
not-enough-data-yet state** ("Still learning — I need a couple more days of normal use.").

### 6.2 Learned habits list
Rows showing habits the assistant has picked up, each tagged **[Active]** or **[Learning]**, each with a
**Forget** control (severs it). Design the row + the tags + a (red/destructive) Forget affordance + a
confirm.

### 6.3 Persona / style constraints
A second list: plain-English statements about the user's preferences/style the assistant has inferred,
each with **Forget**. Same row pattern.

### 6.4 Refresh + states
A manual **Refresh**. States: loading · populated · learning/empty · disconnected.

---

## 7. Cross-cutting component inventory (design a small system)

Please deliver these as reusable components in both light + dark:
- **Buttons:** primary (affirmative), secondary, destructive, and quiet/tertiary; plus icon-only
  buttons. All states: default/hover/pressed/focused/disabled.
- **Tags / pills:** "Auto" vs "Needs approval"; state badges (queued/running/done/failed/held).
- **Status chip** (the live task chip, all states).
- **Tab badge** (numeric count).
- **List row** (the task row; the habit row; the log message row).
- **Progress:** determinate bar (with i/N label) + indeterminate spinner.
- **Inline editable field** (for the path editor).
- **Searchable dropdown/overlay** (the `/` picker; the Change-tool menu).
- **Inline banner/bar** (consent; disclaimers).
- **Empty states** (one per surface), **loading states**, **error states**, **disconnected/offline
  state**.
- **Native notification** styling guidance (the app also posts macOS notifications for alerts/consent
  when the window isn't focused — align tone/voice).

---

## 8. Visual system requirements (you own the specifics; here are the semantics)

- **Color is functional first.** We rely on a consistent state palette: **success/done = green**,
  **in-progress = blue/accent**, **needs-attention/held/scheduled = amber**, **failure = red**,
  **idle/queued = neutral grey**. Refine the exact hues for both modes and AA contrast; keep them calm
  (this tool runs all day).
- **Typography:** define a hierarchy (titles, section headers, body, monospace for results/paths/IDs).
  Results, file paths, and code-like content should be **monospaced and selectable**.
- **Density:** comfortable but information-dense (it's a workspace). Define spacing scale.
- **Iconography:** SF Symbols; provide chosen symbol names.
- **Motion:** subtle. State transitions (queued→running→done) should feel alive but not distracting; tab
  switches instant; progress bars smooth. No bouncy/consumer animations.

---

## 9. Interaction & behavior rules the design must honor

1. **Never hijack the user.** Firing a job does **not** auto-switch tabs or steal focus. Surface progress
   in place (the chip) + an ambient badge; navigation is always user-initiated.
2. **Model proposes, code disposes.** Every job the assistant parses from language is shown to the user
   as a visible row **before/while it runs** — they can catch a mistake and fix it (Change tool / edit
   path). Design must make the parsed job **visible and correctable**, never silent.
3. **Honesty in copy.** The sleep disclaimer and the privacy line are required, visible, non-hidden.
4. **Two action classes.** "Autonomous" (safe, runs itself) vs "Held" (needs approval) must be visually
   consistent everywhere they appear (the `/` picker, the task rows, Activity).
5. **Resilient to disconnection.** When the background engine is down, the UI shows a quiet reconnecting
   state, disables compose, and never looks broken or frozen.

---

## 10. Keyboard & accessibility (required, not optional)

- Full keyboard operation: ⏎ to send, `/` to open the picker, ↑↓⏎Esc in the picker, Tab order through
  all controls, ⌘W to close window.
- VoiceOver labels for every control, the task states, and progress.
- Dynamic Type support; nothing fixed-size that clips at larger text.
- AA contrast in light + dark; never rely on color alone to convey state (pair with glyph + label).

---

## 11. Sample content (design against real data, not lorem ipsum)

- **Catalog verbs:** summarize a document · read a web article · read a file · web search · system
  report · audio status · network status · largest apps · find a file · open an app · open a URL · set
  volume · mute · empty trash.
- **A task, queued→done:** `Summarize — Q3-PRD.pdf` · WORKING `chunk 7/12` · DONE → result panel with a
  multi-paragraph summary.
- **A failed job:** `Read article — /Users/me/PRD.pdf` · ❌ "That's a local file, not a web page — try
  Summarize a document." · [↻ Retry] [Change tool ▾ → Summarize a document / Read a file] [editable path].
- **A scheduled job:** `System report` · 🗓 "Tonight 2 AM (in 6 h 40 m)".
- **Chat exchange:** user "what's slowing my internet?" → assistant answer; user "/summarize the report
  on my desktop tonight" → chip "🗓 scheduled for 11 PM — View on Canvas".
- **Mirror summary:** see 6.1.

---

## 12. Deliverables we want from you

1. **Wireframes** for all three tabs + sub-tabs (low-fi, to agree on layout/IA).
2. **High-fidelity mockups**, **light + dark**, for every tab and **every state** (empty, loading,
   populated, error, disconnected, job running, consent pending, scheduled).
3. **The `/` overlay** and **Change-tool dropdown** designed in full (incl. focused row, search, empty).
4. **A component library / mini design system** (Section 7) with all interaction states.
5. **The task-row component** specified for all six states (the centerpiece).
6. **Iconography choices** (SF Symbol names) for tabs, actions, and states.
7. **Redlines / specs** (spacing, type, color tokens, sizing, min-window behavior, resize/reflow).
8. **An interactive prototype** of the two key flows: (a) compose-a-job-in-chat → chip → Canvas, and
   (b) a failed job → recover via Change-tool / edit-path.
9. **Motion notes** for state transitions and progress.
10. **Window chrome**: toolbar tabs, the menu-bar status icon (connected/disconnected), and the
    active-task badge.

---

## 13. Explicitly out of scope (do not design these)
- A click-to-build action **palette** in the Canvas (removed — language replaces it).
- A full **calendar/date-time picker** for scheduling (presets only for v1).
- Any view of **screen contents, window titles, document text, or URLs** in the Mirror (privacy).
- Cloud/account/login/settings-sync surfaces (there is no cloud).
- Multi-file attachment, multi-user, or mobile layouts.

---

## 13b. Resolved decisions (answers to the designer's clarifying questions)

These are settled — design to them.

1. **Deliver first → an interactive prototype of the real window** (clickable, all 3 tabs, the two key
   flows). The IA is already decided, so skip standalone wireframes; grey-box the first clickable pass if
   helpful, then polish. **Deliver the component library + redlines alongside/right after** (needed for
   implementation).
2. **Light + dark → one prototype with a live light/dark toggle.** Both modes first-class.
3. **Must be fully clickable in the prototype:** (a) compose-a-job-in-Chat → live chip → View on Canvas;
   (b) the `/` action-picker overlay (filter, ↑↓⏎, pin verb token); (c) failed job → Change-tool /
   edit-path recovery; (d) the task state machine animating queued→running→working→done; (e) tab + sub-tab
   switching. **Design as static states only (no need to wire):** inline consent bar, voice recording
   state, disconnected/reconnecting state.
4. **One resolved, faithful native-macOS direction** — with **two variations on just the `/` picker and
   the task-row** (the two novel, highest-stakes components). Everything else: single direction.
5. **Accent: neutral graphite chrome; the functional state colors carry all semantic meaning; defer to
   the macOS system accent only for standard control affordances** (selection, focus rings, default
   button). A decorative blue/cyan brand accent is **forbidden** — it collides with the blue "in-progress"
   state. If any signature tint is wanted, a restrained deep-teal is the only safe option (distinct from
   the blue progress state), but graphite is the recommendation.
6. **Voice/personality:** calm, precise, honest, lightly warm — a competent quiet assistant, not a chatty
   persona. Short sentences, plain words, no hype, always owns its limits, never implies feelings. Use
   these lines verbatim:
   - Empty Chat: "What can I do? Ask me something, or type **/** to run a job."
   - Empty Now: "Nothing running. Start a job from Chat and watch it here."
   - Scheduled disclaimer: "Runs at the set time **if your Mac is awake** — otherwise on the next wake.
     Nothing runs while it's off."
   - Mirror privacy line (always present): "Learned from which app is in front — never your screen contents."
   - Clarification: "I couldn't match that to something I can do — want to rephrase?"
   - Job fired: "Running now — I'll keep this updated." / "Scheduled for 11 PM."
   - Mirror, insufficient data: "Still learning — give me a couple more days of normal use."

---

## 14. One-paragraph summary for the designer
Design a single, native macOS workspace **window** with three toolbar tabs — **Chat** (a conversation
that doubles as a natural-language + `/`-command composer), **Canvas** (a task board where jobs run
through a queued→working→done/failed state machine with inline error recovery and scheduling), and
**Cognitive Identity** (a calm, privacy-forward view of what the assistant has passively learned). It
must feel trustworthy and honest, work in light and dark, never hijack the user's focus, always make the
assistant's parsed intent visible and correctable, and never imply it can see the user's screen contents.
