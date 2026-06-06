# contradiction

## Overview
The C2 **pre-commit hallucination / contradiction check** (PRD C2, milestone M1). Because the
LLM cannot detect semantic absurdity, NARS catches the logical conflicts. The guard does a
**read-before-write**: it queries ONA non-destructively for an incoming claim and computes the
**polarity conflict in Python**, then surfaces *both* sides' evidence trails to the human. It
never auto-blocks and never pollutes the L1 cache with the candidate.

## Why pre-commit (not post-commit + rollback)
ONA emits no contradiction signal (`Truth_Revision` silently merges) and has no un-ingest, so
L1 cannot be rolled back. Checking *before* committing keeps the reasoning cache sterile.

## Scope (verified against ONA)
- ✅ **Direct same-statement polarity contradictions** — system HOLDS `<X --> Y>` false, LLM
  asserts it true (or vice versa), at meaningful confidence.
- ❌ **Transitive-derived negations** — out of scope. ONA zeroes a conclusion's confidence when a
  premise has frequency 0 (`conf = c1*c2*f`), so derived negations don't propagate. Inherited
  constraints must be *materialized* as direct statements (future work).
- ❌ **Competing-value / uniqueness conflicts** (AWS vs GCP) — deferred; handled by `Truth_Revision`.

## Usage
```python
from contradiction import ContradictionGuard
guard = ContradictionGuard(brain, store, on_conflict=present_to_user)
conflict = guard.check("<x --> y>. {1.0 0.9}")   # None, or a Conflict carrying both evidence trails
```

## Tests
From `src/`: `python3 -m contradiction.test_check` (pure) and
`python3 -m contradiction.test_guard` (fake-injected L2 + real ONA).

## Related
PRD C2, R1 (C2 is load-bearing); ADR-001; brain (query), memory (L2 reload).
