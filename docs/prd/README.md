# Product Briefs & PRDs — index

**Convention:** every product brief and PRD lives in this directory (`docs/prd/`), one file per
document, and is registered in the table below. New briefs are added here as part of the same change
that creates them — so this index is always the complete, current record of product intent for
future reference.

| Document | Scope | Status |
|---|---|---|
| [PRD.md](PRD.md) | The foundational PRD — NARS-JARVIS as a local, learning, explainable cognitive assistant. Source of truth for overall scope. | Living |
| [PRODUCT-BRIEF.md](PRODUCT-BRIEF.md) | The original product brief for NARS-JARVIS (vision, audience, value proposition). | Ratified |
| [ADR-049-context-orchestration-brief.md](ADR-049-context-orchestration-brief.md) | The Context Orchestration Layer — tiered verified-actuation backend that gives JARVIS "muscles"; includes the locked 4-step implementation roadmap and the go-gate to the Passive Observer. | Ratified; bootstrap shipped (v1.15.0), remaining steps post-validation |
| [ADR-050-passive-mirror-brief.md](ADR-050-passive-mirror-brief.md) | The Passive Observation Mirror ("What I've noticed about your computer use") + the falsifiable validation experiment (data-sufficiency gate, 4 criteria, decision logic, scope limits). | Ratified & deployed (v1.16.x); in validation |

## How briefs map to implementation
Each brief that drives a build is realized by a numbered **ADR** in [`../adrs/`](../adrs/) (the immutable
decision record) and tagged releases. The brief captures *product intent and the validated design*; the
ADR captures *the decision and its consequences*; the tag captures *what shipped*.

- ADR-049 brief → [ADR-049](../adrs/ADR-049-context-orchestration-layer.md) → v1.15.0 (bootstrap)
- ADR-050 brief → [ADR-050](../adrs/ADR-050-passive-observation-mirror.md) → v1.16.0 / v1.16.1 / v1.16.2
