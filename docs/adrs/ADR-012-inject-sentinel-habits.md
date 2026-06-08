# ADR-012: Inject learned sentinel habits into the LLM context

## Status
Accepted — completes ADR-007 open item (c) (injecting learned sentinel habits, not just live state).
Builds on ADR-010 (dynamic-context injection pattern) and ADR-011 (durable `sentinel_beliefs`).

## Context
The dual brains were still one-directional. ADR-010 injected the sentinel's *live state* (foreground
category) and ADR-011 made its *learned procedural beliefs* durable — but the Knowledge Brain still
didn't know **how the user prefers JARVIS to operate** (the learned autonomy authorizations,
`<distracted_hide_<cat> --> [approved]> {f c}`). Feeding that Narsese to the 7B would be ignored or
mis-read.

## Decision
Translate the sentinel's *confident* procedural beliefs into plain-English directives and inject them
as a distinct **"Learned habits"** block each turn — closing the procedural→declarative loop.

- **Deterministic templating over the closed vocabulary** (`src/context/habits.py`, pure) — no LLM
  call. `habit_directive(term, freq, conf)`: regex-match the habit term (non-greedy `(.+?)` capture so
  snake_case/dashes survive), map the category via a curated `_FRIENDLY` table or sanitize an
  unmapped one with `_humanize` (lowercase, `_`/`-`→space, drop non-alphanumerics, collapse), and
  **fail safe to None** if the result is empty or > 40 chars (never inject malformed grammar).
- **Epistemic filter** via the Narsese expectation `conf·(freq−0.5)+0.5`: confidently favorable
  (E ≥ 0.85) → a positive directive; confidently negative (E ≤ 0.15, an explicit denial) → a negative
  **safety** directive ("you've told JARVIS NOT to…"); the uncertain middle → **omitted**.
- **Sourced from the persisted `sentinel_beliefs`** (ADR-011) via a read-only `SentinelStore` handle
  in `Session`, so habits apply **even when the Flow Sentinel loop is off**. Injected via a
  `habits_provider` callable (DI, mirroring ADR-010's `context_provider`) into `Jarvis.converse`,
  as its own block separate from the ephemeral live context and from taught memory. The system prompt
  marks it as firm boundaries to respect.

## Consequences
- **Gained:** the declarative brain now respects the procedural brain's learned (dis)authorizations —
  e.g. it won't offer to auto-hide a category the user explicitly denied. Dual-brain loop closed,
  fully decoupled (works with the sentinel loop disabled).
- **Layering:** `context` stays pure and does NOT import `service` — the expectation math is re-copied
  locally (as it already is in `service.autonomy` and `sentinel.surprise`).
- **Accepted scope:** v1 vocabulary = category-level autonomy authorizations only (the steadiness
  baseline is machine-state, deliberately excluded). The bridge is extensible — add templates as the
  sentinel learns richer habit types. Negative habits are surfaced as safety walls.
- **Safety of the fallback:** an un-vetted custom category can only ever produce a *missing* directive
  (humanized-then-omitted), never broken/unbounded prompt text.
- **Echo guard:** the habits block lines are added to `converse`'s echo-guard `known` set so the LLM
  cannot re-save a habit as a durable memory (it lives in `sentinel_beliefs`, not `memories`).
- **The LLM is NOT the enforcer (honest caveat, found in live validation):** injecting a negative
  habit makes the model *aware* of the boundary and it recites it correctly, but its verbal adherence
  is probabilistic — when asked directly to do a forbidden action it may still verbally agree. That is
  not a safety hole: the **deterministic autonomy gate** (ADR-006/011, `gate_passes`) is the real wall
  — a denied category's expectation is below the floor, so nothing actually executes regardless of what
  the LLM says. The injected habit is conversational awareness/consistency; the gate is enforcement.
  The prompt asks the model to refuse forbidden actions, but we never rely on that for safety.
  (Live-observed: asked to "auto-hide my IDE," the 7B verbally agreed despite the negative habit;
  `converse` performs no action, so nothing hid.)
- **Cross-store divergence (known, out of scope):** a casual conversational request ("please hide my
  IDE") can be auto-saved (ADR-008) as a conversational preference that *differs* from the procedural
  habit in `sentinel_beliefs`. The two stores are separate trust levels — the procedural gate requires
  *deliberate* consent (a y/n) and is intentionally NOT updated by a casual ask, so the conversational
  note and the gate can disagree. The gate remains authoritative for actions. Reconciling conversational
  memory against procedural habits (and/or routing casual requests into a consent prompt) is future
  work, adjacent to ADR-007 (d) hybrid grounding — deliberately not solved here.

## Alternatives Considered
- **Feed raw Narsese to the 7B:** rejected — ignored/mis-read; a closed vocabulary is a dictionary
  lookup, not an inference task.
- **Read from the live sentinel brain only:** rejected — habits would vanish when the loop is off;
  the durable `sentinel_beliefs` is the better source.
- **Surface all beliefs (incl. uncertain):** rejected — unconverged truths invite hallucination; the
  expectation filter only admits confident positives/negatives.
