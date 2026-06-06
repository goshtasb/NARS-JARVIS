# ADR-002: Execution Safety Model & Trigger-Soundness Predicate

## Status
Accepted

## Context
Capability C4 (habit learning + execution) lets NARS-JARVIS act on the live machine — the
highest-risk surface in the system. Two questions had to be settled before any code is
written, because the architecture does **not** enforce them by itself:

1. **Execution:** how does an abstract NARS operation become a concrete command **without**
   the LLM generating (and potentially hallucinating) a destructive script?
2. **Trigger:** how do we guarantee the *decision to act* is logically sound — not overfit to
   coincidence — given that ONA's shipped defaults are tuned for cheap-mistake game agents?

## Decision — Part A: Execution Safety (the operation-catalog contract)
1. **Finite, pre-authored catalog.** Actions are a bounded set (≤ `OPERATIONS_MAX = 10`) of
   typed, parameterized capabilities registered via ONA's `NAR_AddOperation(name, callback)`.
   NARS **cannot invent a new operator** — there is no code path for it.
2. **NARS emits a symbol, not a script.** Its executive output is
   `(operator_id, argument_term)`, where the argument term is built **only from atoms already
   grounded in memory**. The LLM is upstream (learning / translation) only and is **never** in
   the execution path.
3. **Typed handlers, no raw strings.** Each handler accepts pre-defined enums / IDs / grounded
   atoms — never free text. It **validates** arguments against an allow-list / schema and
   **rejects** unknowns, then **binds** validated arguments into an **immutable, human-reviewed
   command template**. Parameter-binding, not script-generation.
4. **HARD RULE — no generative operations.** No free-form operation (e.g.
   `^run_shell(string)`, or any handler that interpolates LLM output into a command) may ever
   be registered. The most powerful permitted operation is `^run_saved_command(id)`, where
   `id` selects from a curated library of pre-vetted, version-controlled command texts.
5. **OmniGlass is the second gate, not the translator.** The already-concrete, already-validated
   command is submitted to OmniGlass: `sandbox-exec` confinement + confirmation gate + PII
   redaction. Only **reversible** actions are autonomy-eligible.
6. **Autonomy is gated on a passed adversarial OmniGlass sandbox audit.** Until that audit
   passes, the human confirmation click is the system's **primary firewall**, not a feature.

## Decision — Part B: Trigger-Soundness (the autonomy-eligibility predicate)
1. **ONA's defaults are forbidden for real operations.** `DECISION_THRESHOLD_INITIAL = 0.501`
   and `MOTOR_BABBLING_CHANCE_INITIAL = 0.2` are tuned for exploratory game agents where a
   wrong action costs a game point. On a live OS they are catastrophic.
2. **Policy overlay for C4** (overrides defaults):
   - **Motor babbling = 0** — no random exploratory execution, ever.
   - **High confidence floor** (≫ default) — kills few-observation coincidences.
   - **High frequency floor** — kills patterns that often did *not* hold.
   - **Minimum observation count + recency** — no acting on thin or stale evidence.
   - **Minimum human-confirmation count** before an action class becomes autonomous.
3. **Rejection is a training signal, not just a veto.** A declined proposal feeds back as
   negative evidence (NARS's "assumption of failure"), actively **eroding** the implication's
   frequency. The confirmation gate is simultaneously the firewall and the correction loop.

## Consequences
- Hallucinations cannot reach execution: the LLM is structurally decoupled from the action path.
- A future developer cannot "move fast" and detonate the guarantee without violating an
  explicit, reviewable contract (the no-generative-operations rule + the typed-handler rule).
- C4 is reframed from an open-ended security risk into a bounded automation problem.
- The system is slow to grant autonomy by design — many consistent, confirmed observations are
  required. This is an accepted trade of speed for safety.

## Alternatives Considered
- **Let the LLM generate or parameterize commands at execution time.** Rejected — this is the
  exact hallucination surface we exist to eliminate. If an LLM-proposed parameter is ever
  introduced, it must pass the same typed validation and confirmation gate; the validation, not
  the LLM's good behavior, is the boundary.
- **Use ONA's native decision defaults.** Rejected — they are tuned for game agents and would
  act on a single perfect-frequency coincidence (expectation ≈ 0.72 > 0.501).
- **Rely on NARS alone to avoid overfitting.** Rejected — NARS separates rare coincidence from
  frequent regularity, but **not** correlation from causation. A persistent confounded
  correlation will be learned; the predicate + human loop contain it, NARS cannot.

## Acknowledged Limits
- Safety holds **only if** the operation catalog stays bounded and generative-free — a
  discipline enforced in code review, ideally in the type system.
- Final enforcement rests on OmniGlass's sandbox being sound, which is an open question
  (subject to the adversarial audit prerequisite above).
- Threshold tuning (confidence / frequency / discretization) is an empirical problem with no
  principled "correct" value; it is measured, not asserted.
