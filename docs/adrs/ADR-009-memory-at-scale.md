# ADR-009: Memory at scale — embedding-ranked retrieval + slot-aware supersedence

## Status
Accepted — implements ADR-007 "Open/next (a) embedding-ranked memory" and the ADR-008 open items
(forget surface, conflict handling). Builds directly on the embedding pipeline ADR-008 added for the
context-echo guard.

## Context
ADR-008 opened conversation to implicit memory, so the `memories` table grows fast. `Jarvis._recall`
injected the **most-recent ~30** memories **verbatim every turn** into a **4096-token** context — a
time-bomb (overflow / instruction-dilution) — and dedup was **exact-text only** with **no conflict
handling**, so "my name is Ashkan" and "my name is Sam" could both be ground truth at once. There was
no user-facing forget.

## Decision
Two coupled changes, both deterministic (the ADR-008 lesson: never rely on the 7B to resolve a data
conflict at runtime).

**1. Embedding-ranked retrieval.** `MemoryStore.search(query_vec, k)` ranks **active** memories by
cosine to the embedded question (brute-force matmul, mirroring `SqliteGroundingStore` — no vector DB
for a single user's corpus). `_recall(question)` injects the top-k relevant memories (pinned always)
instead of the recency dump; falls back to recency when no embedder is wired (tests/offline).
`remember` now stores an embedding for each memory.

**2. Soft supersedence with tombstones.** New columns on `memories`: `active`, `superseded_by`,
`superseded_at` (additive, idempotent `_migrate` via `PRAGMA table_info` + `ALTER TABLE`). On write,
`_resolve_conflicts` runs the **two-stage, default-safe, zero-new-LLM** pipeline:
- *Stage 1 (cosine, permissive):* narrow to active near-neighbors (slot check is authoritative; cosine
  only prunes for scale; skipped when no embedding → scans active rows).
- *Stage 2 (deterministic slot logic):* `memory/slots.py` holds a **closed, human-authored
  single-valued-slot registry** (same default-deny pattern as `execution/catalog.py`) — name, lives-in,
  age, employer, editor, indentation-preference, timezone… `same_single_valued_slot(a,b)` is True only
  for the SAME slot with a DIFFERENT value → supersede the older (tombstone). **Default: keep both**
  (no slot / multi-valued like "likes" → never supersede). So "tabs vs spaces" supersedes; "tea vs
  coffee" keeps both.

**Echo-guard vs update.** A same-slot/different-value fact is an *update*, not a context-echo, so it
**bypasses** the ADR-008 echo guards (`filter_known`/`filter_semantic`) and reaches the store to
supersede; only non-updates run through the echo guards. (Without this, a name *change* — cosine-close
to the injected old name — would be wrongly dropped as a paraphrase echo.)

**Forget / undo (visible & correctable).** `[[FORGET: …]]` directive (mirrors `[[REMEMBER]]`) plus a
`forget`/`restore` command surface. `forget` is a **soft** tombstone (exact text, else nearest active
memory by embedding); `restore` and `undo_supersede` bring memories back.

**Supersedence-chain semantics — one-hop, invariant-driven (never a transitive cascade).** Invariant:
**≤ 1 active memory per single-valued slot, always.** For A←B←C: `undo_supersede(C)` reactivates C's
*immediate* predecessor (B) and tombstones C (A stays tombstoned); `forget(C)` empties the slot (no
auto-fallback); `restore(X)` reactivates X and tombstones the current slot holder. Predecessor lookup
is one query on the `superseded_by` reverse-pointer — no recursion.

## Consequences
- **Gained:** retrieval scales (relevant top-k, not a growing dump); contradictions resolve to one
  clean current value; memories are correctable (forget/undo) and survive restarts.
- **Migration:** additive/idempotent; existing `jarvis.db` upgrades on next open (`facts` untouched).
- **Accepted limitations:** the slot registry is **finite** — a novel single-valued attribute won't
  auto-supersede until catalogued (**fails safe → keep both**, visible, user-forgettable). Paraphrased
  predicates rely on pattern/claim coverage. Soft tombstones make every supersede/forget reversible,
  so a false positive is never destructive. Symbolic-path (Narsese) uniqueness conflicts remain
  deferred (`contradiction/check.py` is polarity-only) — out of scope here.
- **Open/next:** unify `facts.english` into ranked retrieval too; learned/expanded slot registry;
  surface forget/undo in the SwiftUI UI as a distinct affordance.

## Alternatives Considered
- **Feed both + recency-bias prompt:** rejected — relies on the 7B to resolve at runtime (fragile per
  ADR-008) and grows context unboundedly.
- **Hard-delete on conflict:** rejected — destructive on a false positive; soft tombstone + restore is
  reversible.
- **Per-write 7B slot extraction:** rejected — latency. Cosine-narrow + closed registry is zero new
  LLM calls.
- **Transitive undo cascade:** rejected — would resurrect multiple mutually-exclusive values into one
  slot, breaking the invariant.
