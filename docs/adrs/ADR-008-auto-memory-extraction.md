# ADR-008: Auto-memory — JARVIS remembers facts from natural conversation

## Status
Accepted — implements the **"Open / next: (b) auto-extracting memorable facts from conversation"**
item recorded in [ADR-007](ADR-007-llm-first-brain.md).

## Context
After ADR-007 made the LLM the primary brain and ONA-backed memory a ground-truth provider,
`Jarvis.converse` injected the user's memory into each turn but was **read-only**: it never wrote.
The only write path was the explicit `learn` command, which routes through the strict ingestion
gate (`language/gate.py`). That gate accepts only narrow taxonomic claims (RelationClaim /
PropertyClaim, IsA) and **rejects** questions, actions/tasks, cause/effect, and fused atoms.

Consequence observed in the live DB: the row `<name --> [ashkan]>` / english `"my name is Ashkan"`
persisted *only* because that sentence happens to fit a property-claim shape. To make JARVIS
remember anything, the user had to type `learn …`; saying a fact conversationally was forgotten,
and most natural statements (preferences, "remember to…") would be silently dropped by the gate.

## Decision
During conversation, JARVIS detects memorable items itself and persists them — no `learn` keyword.

**Detection — single-call, LLM-decided.** `ASSISTANT_SYSTEM_PROMPT` instructs the model to embed a
directive in its normal reply for each memorable item, exactly:

    [[REMEMBER: <concise third-person fact>]]

`language/extract.py` (pure, model-free) is the single source of truth for this syntax:
`split_memory_directives(reply)` strips the directives and returns `(clean_reply, [fact, …])`;
`memory_acknowledgment(facts)` builds the visible `(Saved: …)` suffix. Detection adds **no extra
model call** (it piggybacks on the one `generate_text` call) and lets the brain decide memorability
— consistent with ADR-007. Rejected alternatives: routing every utterance through the learn-gate
(extra GBNF call per turn + the gate rejects most memories), a separate intent-classifier call
(doubles latency), and JSON tool-calling (unreliable on the local 7B; clashes with the existing
GBNF `generate` path).

**Persistence — hybrid, guaranteed-recall English store as system of record.** A new `memories`
table in `memory/store.py` (separate from `facts`, so it carries no Narsese constraint) durably
holds each English memory; `MemoryStore.remember/memories_for_recall/forget` manage it, and
`Jarvis._recall` now merges `facts.english` with `memories_for_recall`. For each extracted fact,
`Jarvis._remember_facts` **always** calls `store.remember(...)`, then **best-effort** `self.learn(...)`
to enrich ONA when the fact fits the claim schema — a gate rejection or model hiccup is swallowed
and never blocks the save or crashes `converse`. We deliberately did **not** relax
`facts.narsese NOT NULL UNIQUE` nor synthesize fake Narsese keys (that would pollute ONA and the
`_converse_grounded` evidence trail).

**Policy (user-chosen).**
- *Conservative* extraction: only clear personal facts, stated preferences, and explicit
  "remember…" requests — the prompt says so and shows few-shot examples; the parser caps at 3
  facts/turn and ignores empty/over-long captures.
- *Visible* saves: every NEW save is confirmed inline with `(Saved: …)` so a wrong save is seen and
  can be corrected (`MemoryStore.forget` / `forget_like` are provided). `MemoryStore.remember`
  returns whether the memory was newly created, so revisiting a known fact (e.g. a recall question
  the model re-tags) saves silently — no spurious `(Saved: …)`. Matches the "no silent behavior"
  rule without nagging. On the **voice** path the suffix is shown on-screen but NOT spoken:
  `language.strip_acknowledgment` removes it from the TTS payload only (`say` would otherwise read
  "…Saved the user's name is Ashkan" aloud, breaking the conversational illusion).

## Live validation (Qwen2.5-7B, real daemon over IPC, 2026-06-07)
Verified end-to-end through the actual daemon: a pure question saves nothing; "My name is Ashkan."
and "I prefer tabs over spaces." each emit the tag and confirm `(Saved: …)`; and after **killing
the daemon and starting a fresh process**, "What is my name?" → "Ashkan" and "Do I prefer tabs or
spaces?" → "tabs" — proving recall comes from persisted memory, not in-process model state.
The first live run **failed**: the 7B ignored a purely descriptive instruction. Reliable adherence
required adding **worked User/Assistant examples** (showing the tag lines) to the system prompt;
that is now part of `ASSISTANT_SYSTEM_PROMPT`. Tag emission still depends on the model, so the
parser degrades safely (no tag → behaves exactly as before).

## The context-echo bug (found on a pre-populated live DB, 2026-06-07)
The first live runs used a fresh, empty DB and passed. On the **real** `jarvis.db` (which already
held taught facts injected via `_recall`), the 7B re-tagged its own injected memory as
`[[REMEMBER: …]]` — so an unrelated question ("what % CPU?") spuriously re-saved "the user's name is
Ashkan", an expanding self-pollution loop (a classic RAG/context-injection failure mode). Fix is
three layers, soft→hard:
1. **Prompt (soft):** *"tag ONLY genuinely new info from the current message; NEVER tag anything in
   the Persistent memory section."*
2. **Verbatim guard (hard, cheap):** `language.extract.filter_known` drops facts whose normalized
   form (case / leading article / quotes / whitespace / trailing punctuation) matches an injected
   memory line — catches verbatim/near-verbatim echoes.
3. **Semantic guard (hard, robust):** `filter_semantic` embeds each candidate and drops it if cosine
   ≥ `SEM_ECHO_THRESHOLD` (0.88) against any injected line — catches *paraphrase* echoes the
   normalizer cannot (the live failure was "my name is Ashkan" → "the user's name is Ashkan", which
   normalizes to different strings). Degrades to layers 1–2 when no embedder is wired.
Re-validated live on the exact trigger: no spurious save, DB unchanged (facts/memories steady). The
residual gap is an arbitrary paraphrase scoring < 0.88; it would then dedup over time and is visible
via `(Saved: …)`.

## Consequences
- **Gained:** JARVIS now learns facts/preferences mid-conversation, by voice or typed `ask`, with
  no special command and no added latency; saves survive restarts and are injected on later turns.
- **Migration:** additive and idempotent — `CREATE TABLE/INDEX IF NOT EXISTS` upgrades the live
  `jarvis.db` on next open; `facts` is untouched.
- **Lost / accepted:** the LLM now writes to durable memory autonomously, so a false/incorrect
  auto-save is possible. Mitigated by conservative prompting, the visible `(Saved: …)` confirmation,
  and `forget`. Exact-text dedup only — semantic near-duplicates ("my name is Ashkan" vs "the
  user's name is Ashkan") are not merged (embedding-ranked recall is ADR-007 future work (a)).
- **Open / next:** ~~a `forget` command / `[[FORGET: …]]` directive for spoken corrections;
  embedding-ranked recall instead of recency~~ — **done, see [ADR-009](ADR-009-memory-at-scale.md)**;
  surfacing the save as a distinct UI event.

## Alternatives Considered
- **Learn-gate for everything:** rejected — drops most natural memories (the original bug) and adds
  a constrained model call per turn.
- **Separate classifier / JSON tool-calling:** rejected — extra latency / unreliable locally.
- **Reuse the `facts` table via synthetic Narsese keys:** rejected — pollutes ONA semantics and the
  grounded-path evidence trail.
