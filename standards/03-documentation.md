# S-03 — Documentation Standards

> **Scope**: Documentation structure, co-location rules, Architecture Decision Records
> (ADRs), and the "No God Docs" rule.

---

## 1. The No God Docs Rule

**Documentation files should stay within a recommended ≤ 200 lines** — a soft target and
review trigger, not a hard cutoff (aligned with S-02 §3 and the absolute no-god-file rule in
[00-manifest.md](./00-manifest.md) §1). When a document grows past it, the default is to
split it into focused sub-documents linked from a parent index. Keep a doc longer only when
splitting would fracture genuinely cohesive material, and note why at the top.

### Why
- **Discoverability** — engineers skip monolithic docs; short, focused docs get read.
- **Maintainability** — a 1,000-line doc is nobody's responsibility; a 100-line doc clearly
  belongs to one module.
- **Accuracy** — smaller docs are easier to keep current. Stale docs are worse than none.

---

## 2. Documentation Co-Location

Documentation lives **next to the code it describes**. Do not maintain a separate `/docs`
monolith.

### Required Structure Per Module
```
<module>/
  README.md        # Required — overview, usage, and public-interface summary
  ARCHITECTURE.md  # Optional — architectural decisions specific to this module
```

### Project-Level Documentation
```
/standards/        # Engineering standards (this directory)
/docs/             # Cross-cutting operational docs only
  /adrs/           # Architecture Decision Records
  /runbooks/       # Operational runbooks
```

---

## 3. Module README Structure

Every module must have a `README.md` with these sections:

### Template
```markdown
# <Module Name>

## Overview
One-paragraph description of what this module does and why it exists.

## Usage
How to use this module's public interface. Include import/call examples.

## Key Components
Brief list of major internal parts and their responsibilities.

## Dependencies
What shared modules or external packages this module depends on.

## Related ADRs
Links to any Architecture Decision Records that affect this module.
```

Keep each section concise. If a section would exceed ~50 lines, it warrants its own
sub-document.

---

## 4. Architecture Decision Records (ADRs)

ADRs capture the **why** behind significant technical decisions. They live in `/docs/adrs/`
and follow a sequential numbering scheme.

### When to Write an ADR
- Choosing a framework, library, or major dependency.
- Defining a new architectural pattern or boundary.
- Making a decision that is difficult or expensive to reverse.
- Resolving a technical disagreement with a final decision.

### ADR Template
```markdown
# ADR-<NNN>: <Title>

## Status
Accepted | Superseded by ADR-XXX | Deprecated

## Context
What problem or question prompted this decision?

## Decision
What did we decide, and why?

## Consequences
What are the trade-offs? What becomes easier or harder?

## Alternatives Considered
What other options were evaluated and why were they rejected?
```

### ADR Rules
- ADRs are **immutable** once accepted. If a decision changes, write a new ADR that
  supersedes the original.
- Number ADRs sequentially: `ADR-001`, `ADR-002`, etc.
- Keep each ADR under 150 lines. If more context is needed, link to references.

---

## 5. Inline Code Comments
- Comments explain **why**, not **what**. The code shows what happens; comments explain the
  reasoning.
- Do not comment self-explanatory code.
- `TODO:` for planned improvements (with a brief description). `HACK:` for intentional
  workarounds (with why it is necessary).

---

## 6. Documentation Maintenance
- Review module READMEs during every change that modifies the module.
- Stale documentation must be updated or removed — never left to mislead.
- Per CLAUDE.md Principle 1, documentation changes ship in the **same change** as the code.

---

*Parent: [00-manifest.md](./00-manifest.md)*
