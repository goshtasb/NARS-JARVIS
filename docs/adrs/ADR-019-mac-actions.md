# ADR-019: Conversational Mac actions + system diagnostics (v1)

## Status
Accepted. First "JARVIS does things on the Mac" slice — direct response to "it's useless right now,
let's add features." Implemented; suite 272 → **305** green.

## Context
Until now JARVIS could converse, remember, and watch app-focus, but it could not *act* on the
machine on request (the only wired system command was the autonomous Sentinel's `df -h`). The user
asked for plain-English control across display / sound / apps & web, plus the ability to **report the
machine's state** ("what's my CPU?") and **flag what looks broken**.

The hard constraint is safety: an LLM must never write shell commands. We already solved an
isomorphic problem twice — the `[[REMEMBER]]` directive (ADR-008) for memory, and the closed
operation catalog + `safespawn` seam (ADR-002 / ADR-015) for execution. v1 composes those proven
parts rather than inventing a new mechanism.

## Decision
A new closed-catalog action layer, driven by a directive the LLM emits and a deterministic layer
validates and runs.

- **`[[DO: <action>]]` directive** (`language/extract.py` `DO_TAG` / `split_do_directives`) — mirrors
  `[[REMEMBER]]`. `[[DO: open_url: https://x]]` → `("open_url", "https://x")`; the name is
  lower-cased, the argument kept verbatim. Pure string processing; a malformed tag is a safe no-op.
- **Closed action catalog** (`actions/catalog.py`) — the LLM *proposes*, the catalog *disposes*.
  Each action binds to a fixed argv template or a diagnostics key; an unknown name resolves to `None`
  and is refused. There is **no generative execution path** — only the enumerated actions can run.
- **Vetted execution** (`actions/run.py` → `safespawn.run`) — argv-only, env-scrubbed (ADR-015).
  `perform(name, arg, spawn=…)` takes an injectable spawn so tests record argv with **no OS side
  effects**. `ActionRunner` is the small object injected into `Jarvis`.
- **Diagnostics** (`actions/diagnostics.py`, psutil, no subprocess) — `report_system` returns
  CPU / memory / disk / battery / top-processes plus deterministic **anomaly flags** (`⚠ CPU pegged`,
  `⚠ memory pressure high`, `⚠ disk almost full`, `⚠ battery low`). `system_report(readings=…)` is
  injectable so the flag thresholds unit-test without faking the host.
- **Orchestration** (`jarvis.py`) — `converse` appends the action list to the system prompt (with
  worked examples — the technique that fixed `[[REMEMBER]]` adherence), parses `[[DO:]]`, runs each
  via the runner, and appends the results to the reply. A directive-only reply still returns its
  result (the 7B sometimes emits just the tag). Wired in `service/session.py`.

### v1 action set
`report_system` (diag) · `dark_mode` · `volume_up` · `volume_down` · `mute` · `unmute` ·
`lock_screen` · `open_app <name>` · `open_url <url>` · `web_search <query>`.

### Trust distinction
These are **user-initiated, fixed system-control commands** — a distinct trust class from the
*autonomous* Sentinel actuation, which runs in the deny-default air-gapped `sandbox-exec` profile.
v1 actions are all **benign and reversible**, so they require no per-action confirmation. Destructive
actions are deferred precisely because they would need a confirm round-trip (no GUI for it yet).

### Argument sanitization (defense in depth)
Three independent barriers, because `open`/`osascript` parse their own arguments:
1. **argv-only via safespawn** — every arg is a single argv element; no shell, no word-splitting.
2. **`open_app <name>`** — template `("open","-a",name)` (forces LaunchServices *app-name*
   resolution, not path execution); name must match `^[A-Za-z0-9][A-Za-z0-9 .+-]{0,63}$` and contain
   no `/` or `..` → rejects flag injection (`--args`, leading `-`) and path execution (`/bin/bash`).
3. **`open_url <url>`** must be `^https?://` (rejects `file://` and bare paths); **`web_search`** is
   URL-encoded via `urllib.parse.quote`. Any arg failing its validator returns a refusal string and
   **never spawns** (covered by `actions/test_run.py`).

## Consequences
- **Gained:** JARVIS now performs display/sound/app/web actions and reports + flags system state from
  natural language, on a framework where adding an action is a one-line catalog entry.
- **Tests:** +33 (`actions/test_catalog.py`, `test_diagnostics.py`, `test_run.py`; extended
  `language/test_extract.py`, `test_converse.py`). All action tests use an injected fake spawn → no
  real side effects; only read-only psutil runs live. The ADR-015 AST guard still passes (no raw
  subprocess introduced).
- **Honest limits:**
  - **Probabilistic tag emission** — a 7B won't always emit `[[DO:]]`; worked examples mitigate, and
    the closed catalog makes a wrong/missing tag *safe* (no invalid action runs), not always *complete*.
  - **`dark_mode`** triggers a one-time macOS **Automation (TCC) prompt** for System Events; the user
    must approve it once. The other actions need no special permission.
  - **Brightness / contrast** (the original literal ask) is **deferred** — macOS has no clean public
    API for the display backlight (needs Accessibility or a third-party tool).
  - **"Figure out what's broken" is v1-shallow** — deterministic anomaly flags only. LLM-*reasoned*
    diagnosis over the report is a documented v2 follow-on.
  - Also deferred: Do-Not-Disturb/Focus, clipboard/notes, media play-pause, destructive actions
    (need a confirm UX), and a GUI consent round-trip.

## Alternatives Considered
- **Let the LLM emit shell / AppleScript directly:** rejected — that is arbitrary RCE; the whole
  point of the closed catalog is that the model selects, never authors, the command.
- **Reuse the air-gapped `execution/` sandbox for these:** rejected for v1 — that path is built for
  *autonomous* actuation under the NARS gate; user-initiated reversible controls are a different trust
  class and don't need the sandbox's network/autonomy machinery. (A future merge is possible.)
- **Always inject full diagnostics every turn:** rejected — heavy and noisy; `report_system` is an
  on-demand action. ADR-010's lightweight cpu/mem live-context line stays for ambient awareness.
