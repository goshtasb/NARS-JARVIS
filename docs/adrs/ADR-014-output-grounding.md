# ADR-014: Output grounding — catch self-fact hallucinations on the way out

## Status
Accepted — completes ADR-007 open item (d) for self-facts. The symmetric partner to ADR-013: that
grounded the memory *write* path; this grounds the *answer* path.

## Context
ADR-007 traded the hard no-hallucination guarantee for utility. The remaining gap: the LLM could
answer "You're in London" while the knowledge graph holds the user is in Los Angeles, and JARVIS would
emit the hallucination. We want an output check with symbolic strictness but **no per-turn latency**.

**Two corrections to the originally-discussed design (verified in code):**
1. **`ContradictionGuard` is polarity-only** (`contradiction/check.py` defers competing-value/uniqueness
   conflicts). The canonical example (London vs Los Angeles) is a competing-VALUE conflict it cannot
   catch — the **single-valued-slot logic from ADR-009 (`memory.slots`)** is what catches it.
2. **No GBNF extraction in v1.** `slots.slot_of()` is pure regex; running it over the answer yields the
   answer's self-claim deterministically — **no LLM/ONA on the hot path**, so latency is negligible.

## Decision
A synchronous, pure, relevance-gated output check in `Jarvis.converse`, run just before returning a
normal answer:
- **Held self-facts:** `_held_self_facts()` runs `slot_of` over taught facts (`facts.english`) +
  conversational memories → `{slot_id: value}` (canonical/pinned wins). **Stage-0 pre-filter:** if we
  hold none, skip (the common case).
- **Detect:** `context.grounding.ground_answer(answer, held)` runs `slot_of` over each answer sentence;
  if the answer asserts the SAME single-valued slot with a value that is **neither a containment match
  of nor contained by** the held value, it's a flagrant contradiction (containment guards against
  `slot_of`'s greedy value capture, e.g. "Los Angeles these days" ⊇ "los angeles" → no false flag).
- **Correct, visibly:** on a hit, **suppress the hallucinated answer and return
  `correction_notice(slot, true_value)`** — *"⚠ Correction: you've told me your {label} is "{value}" —
  I'll go with what you've taught me, not a guess."* Because `converse` is non-streamed we hold the
  full reply first, so the user **never sees the hallucination**. Visible over silent rewrite — the
  standing "100% factual / Autonomous ≠ Invisible" mandate (a silent swap would hide a model failure).

## Consequences
- **Gained:** flagrant self-fact hallucinations (location, name, age, employer, editor, timezone — the
  ADR-009 slot registry) are caught and corrected before the user sees them, with full transparency.
  Together with ADR-013, both the write and answer paths are now grounded for self-facts → ADR-007(d)
  closed for that class.
- **Latency:** Stage-0 skip when no self-facts held; otherwise pure regex over a short answer — no
  GBNF, no ONA query on the hot path. Negligible.
- **Accepted limitations (honest):**
  - **Bounded recall** — only catches contradictions phrased so `slot_of` recognizes them
    (e.g. "you live in London" yes; "you're in London" no). A missed one **passes through** (fails
    open — never worse than pre-ADR-014). Higher recall = future targeted GBNF extraction.
  - **Whole-reply replacement** — a multi-claim answer where only one claim contradicts is replaced
    entirely by the correction; surgical span-preservation is future work (for v1's self-fact scope the
    contradicting claim is usually the crux).
  - **Self-facts / competing-value only** — polarity-class contradictions (`ContradictionGuard`) and
    general world-knowledge grounding remain out of scope.

## Alternatives Considered
- **GBNF-extract every answer + query ONA:** rejected — multi-second per-turn tax; defeated by the
  relevance pre-filter + pure-regex detection.
- **`ContradictionGuard` for the location example:** rejected — polarity-only; cannot do competing-value.
- **Async correction:** rejected — show-then-contradict whiplash; the user would read the hallucination.
- **Silent rewrite:** rejected — hides a model failure; we surface it.
