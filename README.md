# NARS-JARVIS

**A private, local-first AI assistant for your Mac that learns your habits, can take actions on your
computer (with your permission), and can work through a queue of tasks overnight while you sleep —
all running on your own machine, with nothing sent to the cloud.**

It pairs two different kinds of "brain":

- a **Large Language Model (LLM)** running locally — the part that understands plain English and writes
  the replies and summaries, and
- a **Non-Axiomatic Reasoning System (NARS / ONA)** — a small, explainable symbolic reasoner written in
  C that acts as durable memory and a cautious "have I seen this enough times to trust it?" gate.

> **In one sentence:** the LLM does the talking and thinking; NARS remembers and keeps it honest; and a
> strict safety layer makes sure nothing risky ever happens without you saying yes.

---

## Table of contents
- [Who is this for?](#who-is-this-for)
- [The big idea, in plain language](#the-big-idea-in-plain-language)
- [Safety first (why you can trust it on your machine)](#safety-first-why-you-can-trust-it-on-your-machine)
- [What it can do today](#what-it-can-do-today)
- [How it's built (for developers)](#how-its-built-for-developers)
- [Getting started](#getting-started)
- [Using it](#using-it)
- [Roadmap — what's next](#roadmap--whats-next)
- [Build on top of it (contributor guide)](#build-on-top-of-it-contributor-guide)
- [Project conventions](#project-conventions)
- [Honest limitations](#honest-limitations)
- [License](#license)

---

## Who is this for?

**If you're not a developer:** think of this as a "JARVIS" for your Mac that lives in your menu bar.
You can chat with it, it quietly learns the things you tend to do at certain times and offers to do them
for you, and you can hand it a stack of documents at night and read its summaries in the morning.
Everything stays on your computer.

**If you're a developer:** this is a local-first cognitive-assistant architecture — a single-threaded
Python daemon (the "brain"), a thin Swift/AppKit menu-bar app (the "body"), a closed/validated action
catalog, a continuation-passing consent state machine, an ONA-backed habit-learning loop, and a
safe-by-construction overnight batch processor. It's built to be **extended by adding modules**, not by
editing working code. Jump to [Build on top of it](#build-on-top-of-it-contributor-guide).

---

## The big idea, in plain language

Most AI assistants are a thin wrapper around a cloud model: they forget everything between sessions and
they can confidently make things up. NARS-JARVIS is designed differently around three ideas:

1. **Two brains, on purpose.** The LLM is brilliant but forgetful and sometimes invents facts. NARS is
   the opposite — it never forgets, it only believes things it has seen enough evidence for, and it can
   *explain why* it believes something. So the LLM handles language and decisions, and NARS provides
   durable memory and a reality check.

2. **It learns by watching, not by scripts.** Instead of you programming rules, the system notices
   patterns in what you actually do ("you mute audio around 4 PM on weekdays when Zoom is open") and,
   once it has seen the pattern enough times, *offers* to do it for you. You're always asked first.

3. **Local and private.** The language model, the reasoner, your memory, and your habits all live on
   your machine. The system is local-first with **one explicit, narrow exception**: a read-only web
   search (ADR-034) that sends your *search query* to DuckDuckGo when you ask it to look something up.
   No local files, memory, or telemetry are ever uploaded, and the autonomous execution sandbox stays
   fully air-gapped (test-locked). See "Safety first" below for exactly what crosses the network.

---

## Safety first (why you can trust it on your machine)

Safety isn't a feature here; it's the architecture. The guarantees:

- **Local-first, with one declared network egress.** No cloud account, no telemetry, no API keys. The
  *only* outbound traffic is the ADR-034 read-only web search/article-read: it sends your search query
  (or a URL you point it at) to DuckDuckGo and reads the result — nothing else leaves. It runs in an
  isolated subprocess (the brain process itself never opens a socket), only does GET requests (it cannot
  log in, submit forms, or write to the web), blocks private/loopback addresses (SSRF guard), and caps
  what it downloads. The **autonomous execution sandbox tier stays fully air-gapped** — a test still
  fails the build if *that* tier ever gains network.
- **It asks before doing anything with consequences.** Reversible things (e.g. toggling dark mode) it
  can do; anything destructive or that controls the GUI goes through an **explicit Approve/Deny consent
  gate**. Approval is always a human click.
- **It can only do things from a fixed, vetted list.** The assistant proposes an action; *code* decides
  whether it's allowed and how it runs. There is no path for the model to run arbitrary commands — every
  action is an enumerated, argument-validated catalog entry executed through one hardened subprocess seam.
- **Unattended = read-only only.** When it works overnight, it will *only* run read-only actions on its
  own (read a file, summarize it, report system status). Anything that changes your system, touches the
  GUI, or is destructive is **held** for you to approve in the morning — by design, it physically cannot
  run unattended.
- **It earns autonomy slowly and loses it fast.** A habit only becomes "armed" (offered) after roughly
  six confirmations; a single "no" collapses it. The cautious math lives in NARS, not the LLM.
- **It tells the truth.** A core project rule: never fabricate. For example, the document summarizer
  processes the *whole* document (not a silent first-few-pages truncation) and, if a file is too big for
  one pass, it *states its coverage* instead of pretending.
- **There's an off switch.** An Emergency Stop in the menu bar shuts the whole system down cleanly.

---

## What it can do today

Each capability links to the Architecture Decision Record (ADR) that documents how and why it was built.
Released in tagged increments **v1.0.0 → v1.8.0**; **435 automated tests** currently pass.

### Conversation & memory
- **Chat in plain English** — ask questions, give it facts to remember. ([ADR-007], [ADR-008])
- **Durable, grounded memory** — facts persist in a local SQLite database and are grounded so the
  assistant doesn't contradict itself or hallucinate. ([ADR-009], [ADR-013], [ADR-014])
- **Push-to-talk voice** — speak to it (offline speech-to-text via whisper.cpp) and it can speak back
  (offline `say`). Toggle from the menu bar 🎙. ([ADR-005])

### Doing things on your Mac (with permission)
- **Conversational actions** — "mute", "dark mode", "open an app", "lock the screen", "empty the trash"
  (the last asks first). A closed, validated catalog; nothing else can run. ([ADR-019])
- **Consent state machine** — a non-blocking Approve/Deny system; risky actions wait for your click and
  never freeze the assistant. ([ADR-020])
- **GUI automation** — it can actually click buttons, move sliders, and toggle checkboxes in other apps
  via macOS Accessibility, always behind the consent gate. Uses a stable code-signing identity so the
  macOS permission grant survives app rebuilds. ([ADR-021], [ADR-024])
- **Self-navigation recipes** — higher-level skills like "set brightness to 40%" that open the right
  settings pane and operate the control themselves. ([ADR-022], [ADR-023])
- **File search** — find files by name via Spotlight, ranked for relevance. ([ADR-025])
- **Web search & reading (keyless)** — ask it to look something up and it searches DuckDuckGo and reads
  the page, then **synthesizes a source-cited answer** (not a raw result dump). Read-only, no API key, in
  an isolated subprocess; the only thing that leaves your machine is the search query. ([ADR-034], [ADR-035])

### Learning how you work (persona)
- **Continuous persona learning** — over idle moments it learns your stable *style* and *focus* (e.g.
  "prefers terse markdown tables, no greetings", "doing local development") and feeds that back into
  every answer as a context prefix, so replies match how you work. Learned on a separate, crash-resilient
  reasoner from a closed, developer-curated vocabulary; it only shapes the prompt — it never runs an
  action. ([ADR-036])

### Learning your habits
- **The Habit Brain** — every eligible action you take becomes evidence on a time-and-context pattern in
  NARS; once a pattern is confirmed enough, the assistant *offers* to do it. It distinguishes a broad
  "tendency" (around 4 PM) from a specific "habit" (in Zoom, on weekdays, around 4 PM) and won't fire a
  Zoom habit while you're in Spotify. ([ADR-026], [ADR-028])
- **A glass-box dashboard** — a menu-bar 🧠 **Habits** panel shows exactly what it's learning, each item
  marked `[Learning] (seen ~N×)` or `[Armed]`, with a one-click **Forget**. No raw math is shown; the
  internals stay encapsulated. ([ADR-027], [ADR-030])

### Working overnight
- **Overnight batch queue** — queue tasks before bed; a durable, restart-proof queue runs the read-only
  ones autonomously and **holds** everything else. ([ADR-031])
- **Document work primitives** — `read_file` (text + PDF) and `summarize_file`, which summarizes a whole
  document using a recursive **Map-Reduce** pipeline (so nothing is silently dropped). ([ADR-032])
- **The Batch Canvas** — a dedicated window to compose a batch: click actions from a palette into a plan,
  each tagged **Autonomous** or **Held** live, then Commit. ([ADR-033])
- **The Morning Briefing** — a menu-bar 🌅 panel showing what ran overnight and the actions held for your
  approval (one click to run them), plus **Clear Completed**. ([ADR-031], [ADR-033])
- **A field-test monitor** — [`tools/overnight_monitor.py`](tools/overnight_monitor.py) logs the daemon's
  memory/CPU/thermals overnight so you can catch a leak or crash by morning.

### Watching your machine
- **A "sentinel"** — a second, isolated reasoner that learns your machine's normal behavior and flags
  surprises (CPU/memory spikes, focus patterns). ([ADR-011], [ADR-016])

---

## How it's built (for developers)

### The two-process design: brain and body
- **The daemon ("brain")** — a headless, **single-threaded** Python process. It owns all reasoning, the
  models, memory, and every store. It speaks a small line-delimited JSON protocol over a Unix socket.
  Single-threaded *by construction* (no locks): long work (the LLM, voice transcription, the overnight
  runner) is advanced cooperatively from a `select()` loop tick or offloaded to child processes whose
  output is multiplexed back in. ([ADR-003])
- **The app ("body")** — a thin Swift/AppKit menu-bar app. It holds the macOS permission grants
  (Accessibility, Microphone) and renders whatever the daemon tells it. It hardcodes **zero** business
  logic — e.g., the Batch Canvas asks the daemon for the action palette and its safety tags. ([ADR-004])

This split is why the UI can never drift from the brain's truth, and why you can talk to the same daemon
from the menu-bar app, a terminal console, or a test client.

### Module map (`src/`)
Each module is cohesive and loosely coupled, with a single public interface (`__init__.py` + `__all__`).
Dependencies flow inward toward `shared/`; modules never reach into each other's internals.

| Module | Responsibility |
|---|---|
| `brain/` | Wraps ONA (the C reasoner): add beliefs, ask questions, get truth values. |
| `language/` | The LLM channel: English ↔ Narsese translation, grounding, free-text generation. |
| `memory/` | Durable SQLite system-of-record (facts + auto-extracted memories) + grounding store. |
| `contradiction/` | Pre-commit guard that flags conflicting facts before they're stored. |
| `consent/` | Pure consent ledger (the data model behind Approve/Deny). |
| `actions/` | The **closed action catalog** + the one place actions reach the OS + document work primitives. |
| `habits/` | Habit quantization (time/day/app buckets) + the durable habit store. |
| `overnight/` | The overnight queue, the durable held-action ledger, and the read-only safety classifier. |
| `sentinel/` | The machine-watching second brain: discretizer, surprise detector, narration. |
| `execution/` | A sandboxed execution tier (closed typed catalog + autonomy predicate); network-locked. |
| `context/` | Renders live context (habits, system state) into prompts. |
| `service/` | The daemon: the `select()` server, the session/dispatch plane, and all the loops. |
| `shared/` | Cross-cutting utilities (e.g. the term sanitizer). |
| `ui/` | The Swift/AppKit menu-bar app, popovers, the Batch Canvas window, and the IPC client. |
| `safespawn.py` | The single sanctioned subprocess seam (argv-only, secret-scrubbed). ([ADR-015]) |

### Key engineering patterns to know
- **Model proposes, code disposes.** The LLM emits `[[DO: <action>]]` directives; the closed catalog
  validates them; unknown or unsafe → refused. No generative execution path exists.
- **Functional core / imperative shell.** Pure logic (quantization, chunking, classification, the gate
  math) is separated from I/O (stores, subprocess, the socket), so the core is trivially testable.
- **Continuation-passing consent.** A risky action returns a *spec* with an on-approve thunk held
  server-side; the consent gate runs it later. The select loop never blocks.
- **Write-through + replay.** ONA has no save/load, so learned truths are mirrored to SQLite and replayed
  into a fresh reasoner on start. ([ADR-011])
- **The safe-autonomous boundary** is one pure function over the catalog's action *kind* — read-only
  kinds may run unattended; everything else is held. It can't be talked past.

---

## Getting started

> **Platform:** macOS (Apple Silicon recommended). It uses Accessibility, the menu bar, and `say`.
> **You will need:** Xcode Command Line Tools, Python 3, and a **local GGUF chat model** you download
> yourself (e.g. a 7B such as Qwen2.5-7B-Instruct). Expect to keep several GB of RAM free for the model
> (observed ~4.5 GB resident for a 7B). Everything runs offline.

```sh
# 1. Python dependencies (local inference + PDF reading; both fully offline)
pip install -r requirements.txt        # local inference + PDF + web reader (all offline-capable)

# 2. Get & build the ONA reasoner (it's upstream, not vendored here — needs clang / Xcode CLT)
git clone https://github.com/opennars/OpenNARS-for-Applications
(cd OpenNARS-for-Applications && sh build.sh)

# 3. Point the daemon at your local model
export NARS_JARVIS_LLM_GGUF=/path/to/your-model.gguf
# optional: export NARS_JARVIS_EMBED_GGUF=/path/to/embedding-model.gguf

# 4a. Try the brain from a terminal (no GUI)
cd src && python3 console.py           # learn / tell / ask / status / quit

# 4b. Or build & launch the full menu-bar app (the "body")
sh src/ui/setup-signing.sh             # one-time: create the stable signing identity (keeps the
                                       #           macOS Accessibility grant across rebuilds)
sh src/ui/build.sh                     # compile JARVIS.app
sh src/ui/restart.sh                   # launch the daemon + app (🔵 appears in your menu bar)

# 5. Run the tests
cd src && python3 -m pytest .          # 446 passing
```

> The reference folders (`OpenNARS-for-Applications/`, `NARS-GPT/`, `OmniGlass/`) and your model weights
> are **not** in this repo (they're large / upstream) — see `.gitignore`. ONA
> ([opennars/OpenNARS-for-Applications](https://github.com/opennars/OpenNARS-for-Applications)) and
> NARS-GPT ([opennars/NARS-GPT](https://github.com/opennars/NARS-GPT)) carry their own (MIT) licenses;
> clone them separately. Their licenses govern that code, not this repo's.

---

## Using it

- **Chat:** click the menu-bar 🔵 → type `learn …`, `ask …`, `tell …`, or just a question. Click 🎙 to talk.
- **See what it's learning:** right-click the menu bar → **🧠 Habits…**. Click **Forget** on anything.
- **Queue overnight work:** right-click → **🗂 Batch Canvas…**. Click `summarize_file` blocks, pick your
  files with *Choose…*, add a `report_system` block, then **Commit + Start**. Optionally start the
  monitor: `nohup python3 tools/overnight_monitor.py --duration 8h --interval 30 &`.
- **Read the results:** in the morning, right-click → **🌅 Morning Briefing…** for what ran and what's
  held; summaries are written to `$TMPDIR/jarvis_overnight/`. Use **Clear Completed** to tidy up.
- **Stop everything:** the menu's **⛔ Emergency Stop**.

---

## Roadmap — what's next

Built so far: a complete **compose → queue → run (safely) → review** overnight pipeline, a
habit-learning brain with a dashboard, conversational + GUI actions behind consent, and voice. Natural
next steps (and things deliberately deferred, stated honestly):

- **More document formats** — `.docx` / `.pptx` extraction (PDF + text already work).
- **Drag-and-drop + a "Context Tray"** — drop a folder of files into the Batch Canvas (today: click-to-add
  + a native file picker; no drag/drop infrastructure yet).
- **Richer habit context** — a third dimension (e.g. part-of-day, power source), *after* field data shows
  the current two dimensions arm reliably in real use.
- **Implicit overnight queue** — let the assistant *propose* a queue from the day's conversation for your
  approval. Requires durable conversation history (not stored today).
- **A scheduler** — auto-start the overnight run at a set time (today: you start it manually at bedtime).
- **Piped task chains** — let one task's output feed the next (today: a flat list of independent tasks).
- **Token-accurate chunking** for summaries (today: a conservative character-based heuristic).

The bigger arc: keep the *autonomous* surface read-only and earned, and keep widening the catalog of safe
things it can do — never by loosening the safety boundary.

---

## Build on top of it (contributor guide)

The architecture is designed so you add capability by **adding a module or a catalog entry**, not by
editing working code. Common extension points:

### Add a new action the assistant can perform
1. Add an `Action(...)` to the closed catalog in [`src/actions/catalog.py`](src/actions/catalog.py)
   (choose a `kind`; set `confirm=True` if it's destructive).
2. Implement how it runs in [`src/actions/run.py`](src/actions/run.py) — read-only actions return text;
   destructive ones return a `ConsentSpec` so the consent gate runs them on approval.
3. That's it: the menu-bar prompt, the consent flow, the Batch Canvas palette, and the overnight
   safety tag all pick it up automatically. Add a test next to the others.

### Add a read-only "work" primitive (runs overnight)
- Put the pure logic in [`src/actions/documents.py`](src/actions/documents.py) (the Map-Reduce summarizer
  is the model), declare it `kind="work"` in the catalog, and it's automatically allowed to run unattended
  via the [`overnight/classify.py`](src/overnight/classify.py) boundary. **Honesty rule:** never silently
  drop data — report coverage.

### Add a daemon command + UI surface
- Add a handler to the dispatch table in [`src/service/session.py`](src/service/session.py) returning
  plain JSON, then call it from Swift via `JarvisClient.call(...)`. Keep all logic in the daemon; the UI
  only renders. The Habits dashboard and Batch Canvas are worked examples.

### Ground rules for contributions
- **Read [`standards/00-manifest.md`](standards/00-manifest.md) first** — it routes you to the binding
  sub-standards (modular decomposition, SOLID + functional-core/imperative-shell, file-size guidance,
  documentation). Don't fabricate rules; if one isn't defined, ask.
- **One ADR per feature.** Write a short Architecture Decision Record in [`docs/adrs/`](docs/adrs/)
  capturing the decision, the rejected alternatives, and the honest limits.
- **Document alongside the code** (Principle 1) and **keep modules cohesive, no "god files"** (Principle
  2 & 3). See [`CLAUDE.md`](CLAUDE.md).
- **Tests are the contract.** The pure core is unit-tested; safety boundaries (consent, the read-only
  classifier, no-network) are explicitly asserted.

---

## Project conventions
- **Source of truth for scope:** [`docs/prd/PRD.md`](docs/prd/PRD.md).
- **Engineering rules:** [`CLAUDE.md`](CLAUDE.md) + [`standards/`](standards/).
- **Architecture history:** [`docs/adrs/`](docs/adrs/) — **ADR-001 through ADR-033** (ADR-029 is
  intentionally skipped; a cloud/Drive integration was proposed and dropped to preserve the local-first
  air-gap). Each module also has its own `README.md`.
- **Releases:** annotated tags `v1.0.0` → `v1.8.0`, each tied to its ADR(s).

---

## Honest limitations
- **macOS only**, and the GUI/voice features need a real screen + the granted permissions (the daemon's
  logic is headless-testable, but the windowed UX is human-verified).
- **The local 7B is a 7B** — summaries are useful scaffolding, not a human analyst, and quality/endurance
  over long overnight runs is something this project measures rather than assumes.
- **You bring the model** — no weights are shipped; offline-only by design.
- **Single machine, single user.** No multi-user, no remote access, no scheduler yet.
- Summaries can't extract text from scanned/image-only PDFs (it says so rather than invent text).

---

## License
**MIT** — see [LICENSE](LICENSE). Use, modify, and redistribute freely (incl. commercially) with
attribution. The bundled work is this repository only; the upstream reasoners (ONA, NARS-GPT) you clone
separately are governed by their own (MIT) licenses.

---

<!-- ADR links -->
[ADR-003]: docs/adrs/ADR-003-headless-daemon-ipc.md
[ADR-004]: docs/adrs/ADR-004-macos-menubar-ui.md
[ADR-005]: docs/adrs/ADR-005-voice-pipeline.md
[ADR-007]: docs/adrs/ADR-007-llm-first-brain.md
[ADR-008]: docs/adrs/ADR-008-auto-memory-extraction.md
[ADR-009]: docs/adrs/ADR-009-memory-at-scale.md
[ADR-011]: docs/adrs/ADR-011-sentinel-persistence.md
[ADR-013]: docs/adrs/ADR-013-hybrid-grounding.md
[ADR-014]: docs/adrs/ADR-014-output-grounding.md
[ADR-015]: docs/adrs/ADR-015-security-hardening.md
[ADR-016]: docs/adrs/ADR-016-sentinel-observability.md
[ADR-019]: docs/adrs/ADR-019-mac-actions.md
[ADR-020]: docs/adrs/ADR-020-unified-consent.md
[ADR-021]: docs/adrs/ADR-021-ax-automation-core.md
[ADR-022]: docs/adrs/ADR-022-self-navigation.md
[ADR-023]: docs/adrs/ADR-023-navigation-recipe-catalog.md
[ADR-024]: docs/adrs/ADR-024-phase2-bounded-agent-loop.md
[ADR-025]: docs/adrs/ADR-025-file-search.md
[ADR-026]: docs/adrs/ADR-026-habit-brain.md
[ADR-027]: docs/adrs/ADR-027-habit-introspection.md
[ADR-028]: docs/adrs/ADR-028-multi-variable-habit-context.md
[ADR-030]: docs/adrs/ADR-030-habit-menu-bar-dashboard.md
[ADR-031]: docs/adrs/ADR-031-overnight-batch-queue.md
[ADR-032]: docs/adrs/ADR-032-work-primitives.md
[ADR-033]: docs/adrs/ADR-033-batch-canvas.md
[ADR-034]: docs/adrs/ADR-034-web-search.md
[ADR-035]: docs/adrs/ADR-035-web-answer-synthesis.md
[ADR-036]: docs/adrs/ADR-036-continuous-persona-learning.md
