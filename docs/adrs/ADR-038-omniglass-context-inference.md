# ADR-038: OmniGlass context inference via native OCR & 3B classification

## Status
**Proposed — DRAFT, deliberately unmerged.** Architecture and vocabulary ratified 2026-06-10; this
document is held on its branch until the v1.8.0 field-test review (~2026-06-16) confirms Habit Brain
convergence, and **activation requires a separate explicit go-ahead** after merge. The Habit Brain must
be stable before it starts receiving OmniGlass context evidence.

## Context
JARVIS learns habits, learns persona, executes gated actions, searches the web — but cannot see the
desktop. The Flow Sentinel deliberately stops at the coarse foreground app *category* (`sensor.py`:
"never window titles/contents — exactly the line that keeps us out of macOS TCC dialogs"). This ADR
consciously crosses that line — the **second Rubicon** (the first was network egress, ADR-034). The
macOS **Screen Recording TCC permission is the heaviest privacy ask a desktop app can make**, and the
crossing is justified only by the Ephemeral Privacy Boundary below.

Two findings shape the design:
1. **No vision-language model is needed.** The OmniGlass pipeline (sibling repo) is: screenshot →
   **Apple Vision `VNRecognizeTextRequest`** (native, on-device, milliseconds) → *text* → LLM
   classification. This turns an expensive multi-modal problem into cheap text-in/text-out, viable on
   a 16 GB Metal box (7B ≈ 4.5 GB RSS resident; 3B ≈ 2.1 GB on disk).
2. **The division of labor is the project thesis.** Neural = perception (*what text is on screen*);
   symbolic = reasoning (*what it means*). App focus is already deterministic (Sentinel, ADR-028);
   intent is unobservable from a static frame. The 3B classifies only observable content.

## Decision

### Pillar 1 — The Native Bridge
The daemon drives an unprivileged Swift helper (the Sentinel helper pattern): capture the frontmost
display **in memory**, run `VNRecognizeTextRequest` in-process, emit **only the extracted text** over
the existing stdout-pipe protocol. Pixels never cross the process boundary; Python never sees an image.

### Pillar 2 — Trigger discipline & GPU scheduling
Neither pure polling nor pure reactivity. Costs are asymmetric — capture is cheap, OCR is milliseconds,
the 3B is the only expensive stage — so each stage gets its own trigger:
- **Capture: reactive, debounced.** Fires on the Sentinel's existing `activate` (app-switch) events,
  **5 s after the last** activate (alt-tab storms collapse to one capture; the new window has painted).
  A **300 s dwell re-sample** while the app is unchanged catches *intra-app* shifts (a stack trace
  appearing in the terminal) — without it, `screen_content` degenerates into a noisy copy of the
  deterministic app category. A **60 s minimum cooldown** bounds the worst case. No capture when the
  screen is locked / display asleep; an optional config pauses capture on battery power.
- **Novelty gate: deterministic, free.** The OCR text is normalized and hashed against the previous
  capture; an unchanged screen is dropped **before** the 3B. The expensive stage runs on *content
  novelty*, not on time and not on every switch. (Thresholds 5/300/60 are proposed; locked at acceptance.)
- **Classification: idle-gated, batched** — the `persona_loop` pattern exactly (idle ≥ 45 s, bounded
  batch), so the 3B never contends with a live 7B turn for the GPU.
- **Prompt-prefix caching.** Both models carry a static prefix (system prompt + closed catalog) that
  llama.cpp re-prefills every call today — the measured 5–10 s turn latency is mostly this. The 3B
  classifier keeps its own resident `Llama` instance (lazy-loaded on first batch) with prompt caching
  enabled so per-batch prefill is only the dynamic OCR suffix; the same mechanism is applied to the 7B
  conversational path as an embedded perf task. Acceptance requires measured before/after prefill cost.

### Pillar 3 — The Ephemeral Privacy Boundary
- Pixels exist **only in the helper's volatile memory** and are purged the instant
  `VNRecognizeTextRequest` returns. Never written to disk, tmpfs included.
- Raw OCR text lives **only in the bounded in-memory batch buffer** and is purged the instant the 3B
  classification of its batch completes. It is never persisted, logged, or echoed to any UI.
- **Only the gated Narsese term** (`<screen_content --> …>` + truth value) reaches SQLite — the same
  write-through checkpoint pattern as ADR-036.
- Fail-closed: TCC permission denied/revoked, helper crash, or 3B load failure ⇒ the layer goes DOWN
  (the `persona_loop` `BrainUnavailable` pattern) — no capture, no injection, logged once.

### Pillar 4 — The Observable Content Vocabulary (closed, ratified 2026-06-10)
| (predicate, value) | OCR signature |
|---|---|
| `screen_content --> source_code` | syntax keywords, brackets, indentation |
| `screen_content --> error_diagnostics` | stack traces, exception names, exit codes |
| `screen_content --> technical_docs` | API signatures interleaved with explanatory prose |
| `screen_content --> prose_document` | continuous natural-language paragraphs |
| `screen_content --> communication` | message threads, timestamps, From/To/Re: headers |
| `screen_content --> terminal_output` | shell prompts, command output, log lines |
| *(no clear signature)* | `[]` — the mandatory negative anchor |

A `vocab.py` twin with the same deterministic gate: out-of-vocabulary output is dropped before the NAR.
The extractor prompt ships **with few-shot anchoring + the negative anchor from day one** — measured on
the persona extractor at 58.8%→94.1% recall, 100% precision (v1.11.1) — and a `test_context_recall.py`
harness (synthetic messy-OCR fixtures, opt-in live-3B run) exists **before** any tuning claim is made.

**Intent is derived in NARS, never asserted by the 3B.** The reasoner fuses deterministic
`foreground=dev` ∧ neural `screen_content=error_diagnostics` ∧ the hour bucket → *debugging*, as
revisable symbolic evidence. Contradictory pairs (e.g. `source_code` while foreground is `media`) are
discounted by the same fusion instead of trusted.

### Pillar 5 — The Glass Box (ADR-027/-037 pattern)
The 🧠 Cognitive Identity dashboard gains a **Desktop Context** section: the current believed context in
plain English with its `[Active]`/`[Learning]` state, a red **Forget** per term (SQLite delete + crater
the belief `{0.0 0.9}` in the isolated ONA — the exact `persona_forget` mechanism), and a master
**pause/resume capture** toggle surfaced in the same pane. The user can always see what the machine
believes their desktop context is, and sever it with a click.

## Consequences
- **Gained:** JARVIS perceives desktop context with zero VLM cost; habit/persona evidence gets a
  what-are-you-doing dimension; intent lives in the explainable symbolic brain, not an LLM guess.
- **Paid:** the Screen Recording TCC ask (documented honestly in the README, as ADR-034 retired
  "nothing leaves your machine"); ~2.1 GB additional resident RAM when the 3B classifier is loaded;
  bounded battery cost (debounce + novelty gate + idle gating are the mitigations).
- **Risk accepted:** OCR of dense screens is noisy; the closed vocabulary + negative anchor + NARS
  fusion bound the damage to *at most a wrong known dimension*, never an arbitrary belief.
- **Deferred:** multi-display policy (frontmost display only in v1); non-text perception (no VLM).

## Alternatives Considered
- **Local vision-language model** — rejected: no VL GGUF on disk, ~4–8 GB extra RAM, slow multi-modal
  inference; native OCR + text classification achieves the goal asymmetrically cheaper.
- **3B classifies application focus** (`context --> coding_ide`) — rejected: the Sentinel already
  provides app category deterministically and hallucination-free; paying inference for a noisy copy of
  a free exact signal is an anti-pattern.
- **3B infers intent** (`intent --> debugging`) — rejected: intent is temporal; a static frame cannot
  evidence it. Asking the most hallucination-prone component to assert the least observable thing is
  how false beliefs are born. Intent is a NARS derivation.
- **Pure 60 s polling** — rejected: an hour in one app = ~60 redundant capture→OCR→3B cycles for zero
  information; worst battery profile.
- **Pure app-switch reactivity** — rejected: captures race the window paint, alt-tab storms multiply
  cost, and intra-app content change — the entire value of `screen_content` over app category — is
  never observed.
- **Screenshots to disk / tmpfs files** — rejected: any persisted pixel is a privacy liability and
  violates the Ephemeral Boundary; the helper OCRs in-process from memory.
