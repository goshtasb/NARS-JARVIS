# ADR-036: Continuous Persona-Concept Learning via Bounded Ingestion

## Status
**Accepted** (locked 2026-06-10) — targets **v1.10.0**. Supersedes the originally-numbered "ADR-035" PRD
(that number was already taken by web answer synthesis). Reframed per the v1.9.1 prototype audit:
reuse what exists, fix the PRD's ONA-mechanics errors, keep safety deterministic. The four open questions
below were ratified: thresholds locked as proposed; persona vocabulary locked as a **closed, developer-
curated list** (no automatic expansion); persona kept a **distinct subsystem** from the Habit Brain; and
the partial ADR-007 walk-back (an isolated ONA shaping the prompt, never gating actions) is accepted.

## Context
We want the assistant to continuously learn the user's *persona* (stable style/preferences/focus) and
feed it back into every 7B generation, so output matches how the user works — without re-specifying
already-built infrastructure, paralyzing the single-threaded daemon, or weakening the deterministic
safety boundary. A live prototype against our real engine (2026-06-10) established the empirical basis:

- **The loop works:** an ONA-derived persona constraint, injected as a system-prompt prefix, measurably
  steered the live 7B (a summarize task lost its "Sure!… Feel free to adjust!" greeting/filler when the
  prefix said "omit greetings"). Injection adds ~no latency.
- **ONA variable queries return only the single highest-confidence binding**, not the full set — so a
  live `<user_preference --> ?x>?` would silently drop most of the persona. → **inject from SQLite, not
  from a live ONA query.**
- **Malformed Narsese crashes the NAR subprocess** (`BrokenPipeError`) — the extractor must emit
  validated Narsese, and the ONA wrapper must restart fail-closed.
- **Memory is already bounded** by ONA's compile-time `CONCEPTS_MAX 4096` (the PRD's `volume=10000`
  controls verbosity, not capacity); `priority`/`durability` are dump-only, not restorable.

## Decision
A continuous, **bounded-batch** ingestion pipeline feeding a **dedicated, isolated persona ONA
instance**, write-through to SQLite, with the LLM prompt injected from SQLite (not ONA). The
conversational L1 brain (`session._brain`) is untouched — persona gets its own ONA, mirroring the
sentinel's second-brain isolation, so persona concepts never crowd the memory bag.

```
[user cmd / action result / scraped text] ─append O(1)→ [persona_events_pending (sqlite)]
                                                              │  (only when daemon IDLE or overnight active)
                                                              ▼ pop ≤5, ONE 7B extraction call/batch
                                                   [validated single-brace Narsese]
                                                              ▼ feed
                                            [persona ONA (isolated, resilient wrapper)]
                                                              ▼ write-through (term,freq,conf)
                                                       [persona_concepts (sqlite)]
                                                              ▼ SELECT … WHERE confidence ≥ 0.75
[7B system prompt] ◀── render_persona() ◀── persona injector (fast O(1) read; NO ONA on hot path)
```

### 1. The Ingestion Throttle (protect the single-threaded loop)
- **Buffer:** events append O(1) to a durable SQLite table `persona_events_pending` (survives restart).
  Producers: `jarvis._run_actions` (commands/results) and `web.py`/`documents.py` (scraped/read text),
  via a thin `persona.observe(text, kind)` call — non-blocking, just an INSERT.
- **Drain trigger (idle-gated):** `service/persona_loop.py.tick()` runs from the daemon tick **only when**
  `now − session.last_request_at ≥ IDLE_SECONDS` **or** the overnight runner is active — never while a
  user request is in flight. Proposed `IDLE_SECONDS = 45` (tunable).
- **Batch:** pop **≤ 5** pending events and do **ONE** combined 7B extraction call per batch (not per
  event), then mark them consumed. At most **one batch per tick**. This bounds 7B cost to one blocking
  call per idle window — acceptable because nobody is waiting.
- **Extractor (`persona/extract.py` template, run via the existing LLM):** strict **JSON→Narsese
  deterministic formatting** — the 7B returns a small JSON array of `{predicate, value, freq, conf}`
  objects drawn from a **closed persona vocabulary** (below); code renders them to validated single-brace
  Narsese (`<user_<predicate> --> <value>>. {f c}`). Anything not in the vocabulary is dropped. The 7B
  never hand-writes raw Narsese (that's what crashed the NAR).

### 2. The Unified Checkpoint Schema (`persona/store.py`)
Restorable tuple only (priority/durability dropped — ONA recomputes them):
```sql
CREATE TABLE persona_concepts (
    term        TEXT PRIMARY KEY,           -- e.g. '<user_preference --> terse_markdown_tables>'
    frequency   REAL NOT NULL,
    confidence  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE persona_events_pending (       -- the O(1) ingestion buffer
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'event',
    created_at  REAL NOT NULL
);
```
- **Write-through:** after each batch is fed to ONA, the loop queries ONA for the affected terms' current
  `(freq, conf)` and upserts `persona_concepts` (the ADR-011 pattern, generalized).
- **Replay on boot / after a crash:** re-feed every row as `term. {freq conf}` into a fresh persona ONA
  (ONA has no load command — re-feeding beliefs is the only restore path; this is exactly what
  sentinel/habits already do).
- **Bounded growth:** persona_concepts is keyed by term and pruned (drop rows with `confidence < 0.1`
  during the idle sweep), so the SQLite side stays small; ONA stays under its fixed 4096-concept cap.

### 3. The Resilience Wrapper (`brain/ona.py`, fail-closed)
- Detect a dead NAR (`BrokenPipeError` on write, EOF on read, or `proc.poll() is not None`).
- On failure: relaunch the `./NAR shell` subprocess and **replay beliefs from SQLite**, then retry the
  operation **once**. Bounded by `MAX_RESTARTS` per session (proposed 3) to prevent a crash loop.
- If restarts are exhausted or relaunch fails: raise `CognitiveLayerDown`; `persona_loop` catches it,
  logs `[COGNITIVE LAYER ERROR: …]` to the terminal canvas, and **disables persona injection** — every
  `converse`/overnight pass then runs **statelessly** (no prefix). Failure-closed = degrade to today's
  behavior, never crash.
- Scope: the wrapper benefits any ONA user, but only the persona path adopts the degrade-to-stateless
  policy; the conversational L1 brain keeps its current behavior.

### 4. The Persona Injector (`context/` + `converse`/overnight)
- `persona.current() -> list[(term, freq, conf)]` = `SELECT … WHERE confidence ≥ 0.75` (proposed floor,
  matches the imprinting test). Fast O(1) SQLite read; **no ONA round-trip on the hot path**.
- `persona/vocab.py` (pure) maps each closed `(predicate, value)` to a one-line English constraint and is
  the ONLY thing that becomes prompt text. `context.render_persona()` emits the
  `[COGNITIVE CONTEXT CONSTRAINTS]` block; `converse()` (and the overnight runner's generation step)
  prepend it to the system prompt — slotting into the existing context-block mechanism (ADR-010/012).

## Safety guardrails
- **Deterministic boundary is absolute (ratified):** persona shapes the *prompt only*. It NEVER gates an
  action. The closed catalog + `safe_autonomous` + consent/Held ledger remain the sole firewall; NARS
  truth values decide nothing about execution. (We explicitly reject the PRD's "NARS out-votes a
  destructive goal" mechanism.)
- **Closed persona vocabulary bounds prompt-injection:** untrusted scraped text can only ever nudge a
  *known* persona dimension, and only after crossing the 0.75 confidence floor over repeated batches —
  it cannot inject an arbitrary instruction into the system prompt. Unknown predicates/values are dropped.
- **Isolated persona brain:** separate ONA instance, so persona learning cannot evict or corrupt the
  conversational memory/grounding bag.

## Test plan (Phase-1 verification)
1. **Imprinting & recall:** repeated persona evidence → `persona_concepts` row reaches `confidence ≥ 0.75`
   → appears in `context.render_persona()` output. (Mechanism prototype-verified: 6× → conf 0.98.)
2. **Injection effect & closed-vocab:** a row above floor is present in the converse system prompt; an
   unknown/garbage term in the table is NOT injected.
3. **Resilience / fail-closed:** feed malformed Narsese → NAR crashes → wrapper restarts + replays from
   SQLite → next op succeeds; exceed `MAX_RESTARTS` → `CognitiveLayerDown` → persona injection disabled,
   logged, and `converse` still answers (stateless).
4. **Throttle:** ingestion does not run while a request is active or before `IDLE_SECONDS`; a batch is
   ≤ 5 events and makes exactly one 7B call; pending events persist across a restart.
5. **Bounded memory (AIKR):** flooding distinct events keeps daemon RAM flat (ONA fixed at
   `CONCEPTS_MAX`); `persona_concepts` stays bounded via the `confidence < 0.1` prune. (Note: the PRD's
   "set bag to 50" test needs a NAR recompiled with `CONCEPTS_MAX 50`; we test the SQLite-side prune +
   that ONA holds under its fixed cap, which is the real guarantee.)

## Locked decisions
1. **Thresholds (locked):** `IDLE_SECONDS=45`, `BATCH_MAX=5`, inject floor `0.75`, prune floor `0.10`,
   `MAX_RESTARTS=3`.
2. **Persona vocabulary (locked, closed, developer-curated — no automatic expansion).** The initial
   `persona/vocab.py` closed set:
   - `<format_directive --> terse_markdown_tables>`
   - `<format_directive --> omit_greeting_prose>`
   - `<format_directive --> cite_sources_explicitly>`
   - `<current_focus --> local_development>`
   - `<current_focus --> unverified_data_synthesis>`

   The extractor may ONLY emit these (predicate, value) pairs; anything else is dropped. Expanding the
   list is a human code commit, never runtime/LLM-driven.
3. **Distinct from the Habit Brain (locked):** habits = behavioral/action (time/app → action proposals);
   persona = semantic/style (prompt constraints). Separate ONA instances, separate SQLite tables.
4. **ADR-007 walk-back (accepted):** an isolated persona ONA shapes the LLM *prompt* only; the LLM remains
   the final arbiter of generation and deterministic code remains the action firewall.

## Alternatives considered
- **Live ONA variable query for injection:** rejected — returns one binding, drops the rest (prototype).
- **Shared (conversational) ONA brain:** rejected — persona concepts would compete for the 4096-concept
  bag and could evict memory/grounding; use an isolated instance.
- **7B extraction per event, on the hot loop:** rejected — 5–10 s blocking per event; batch when idle.
- **Storing/ restoring priority & durability:** rejected — ONA can't restore them; informational only.
- **NARS attention as a safety gate:** rejected — unsound; deterministic code remains the firewall.
