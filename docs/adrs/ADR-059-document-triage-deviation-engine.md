# ADR-059: Document Triage & Corpus-Aware Deviation Engine

## Status
Accepted

## Context
In-house legal/compliance teams reviewing born-digital commercial agreements (NDA/MSA/DPA) need to know, in
seconds, how a new contract's operative terms ("notify within 72 hours", "$5M liability cap") compare to the
terms they have accepted before — without sending confidential documents to any cloud, and without relying
on stale-able market-norm data (which crosses into legal-advisory liability). Two questions had to be
answered: (1) what does a "deviation" compare against — market norms or the user's own history? (2) where
does the LLM sit, given it hallucinates and is heavy and the comparison must be trustworthy?

## Decision
A **dual-engine** design with a strict boundary.

- **Deterministic symbolic engine (the moat).** Parameter normalization and comparison are plain,
  deterministic Python over a SQLite parameter store — `statistics.median` per-`(clause_type, role, kind)`
  cohorts (never blended) + a **partial-order** comparator. The "standard" is the user's **own corpus**,
  never market norms. The comparator is a partial order, not a scalar: business days canonicalize to a
  calendar floor with an open upper (`n business_days ≥ n calendar_days`, no holiday calendar);
  length-ambiguous units (months/years) become intervals (no false precision); qualitative terms
  ("promptly") are nullified and declared incomparable (the qualitative firewall); non-duration magnitudes
  (money/%) are reported as neutral facts, not ranked (Mirror-not-Advisor).
- **LLM only at one boundary.** `extract.py` is the single sanctioned model touch — GBNF-constrained, 3×
  temperature-consensus, source-grounded (`verify_gate`). An **AST guard** (CI-enforced, `test_map.py`)
  fails the build if any other `triage/` module imports a model/LLM/AGPL dependency.
- **This is NOT NARS.** The deviation math is symbolic-deterministic Python; ONA/Narsese is a *separate*
  feature (belief distillation). External messaging must not claim NARS computes the deviation.
- **Off-loop + memory-managed.** The slow extraction runs in `service/triage_worker.py` (its own model
  subprocess), AC-gated and serial, with the conversational model lazy-evicted so a scan never stacks two
  heavy models. Results surface as a server-authored `deviation_scan` event in the Swift **Risk** panel
  (states pending/populated/empty/deferred; render classes; page citations; canonical-bounds reasoning).
  Bulk onboarding ("connect a folder") enqueues `triage_file` tasks so the per-kind baseline compounds.

## Consequences
- **Easier:** the result is reproducible, citable, and **hallucination-free by construction** (the math
  never runs a model); adding a clause type or parameter kind is a lexicon/enum edit; the corpus compounds
  with zero market-data liability.
- **Harder / costs:** extraction quality is bounded by the LLM at `extract.py`. A general small model (0.5B)
  was empirically too weak — it hallucinated a duration from a section number ("3. Indemnification" → "3
  business days") — so triage currently needs a capable model (7B) and is a **16 GB+ tier** feature until a
  task-specific extractor lands (tracked: GitHub #24). A separate known issue: length-ambiguous units
  (months/years) over-report identical values as "differs" (interval-vs-point comparison; tracked).

## Alternatives Considered
- **NARS/ONA for the deviation math** — rejected: the comparison is simple SLA arithmetic; a symbolic
  reasoner adds opacity, not value, and claiming it would be false. NARS is reserved for belief distillation.
- **Market-norm baselines** — rejected: stale jurisdictional data is a legal-advisory liability; the user's
  own corpus is defensible and private.
- **Scalar unit conversion** ("3 business days = 72h") — rejected: fabricates precision and needs a
  stale-able holiday calendar; the partial order with an explicit unrankable element is honest.
