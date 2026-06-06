# Engineering Standards — Master Manifest

> **Purpose**: This is the central referral index for all engineering standards
> in the NARS-JARVIS project. No single document contains all rules. Each
> standard is modular, focused, and independently maintainable.

---

## How to Use This System

### For AI Agents

Before beginning **any** architectural, refactoring, or feature-development
task, follow this protocol:

1. **Read this manifest first** to identify which sub-standards apply.
2. **Read only the relevant sub-standard files** listed below based on your
   task type.
3. **Do not fabricate rules.** If a rule is not defined here, ask the user
   rather than assuming.

| Task Type                        | Required Reading                  |
| -------------------------------- | --------------------------------- |
| New feature / module creation    | `01-architecture.md`, `03-documentation.md` |
| Code writing / modification      | `02-coding-practices.md`          |
| Refactoring / restructuring      | `01-architecture.md`, `02-coding-practices.md` |
| Documentation changes            | `03-documentation.md`             |
| Full-stack feature (end-to-end)  | All sub-standards                 |

### For Human Engineers

Read each sub-standard during onboarding. Refer back to specific files during
code review or architectural discussions. Each file is designed to be read in
under 5 minutes.

---

## Standards Index

| ID   | File                          | Scope                                  |
| ---- | ----------------------------- | -------------------------------------- |
| S-01 | [01-architecture.md](./01-architecture.md)         | Modular decomposition, module boundaries, public interfaces |
| S-02 | [02-coding-practices.md](./02-coding-practices.md) | SOLID, functional core, file limits, naming conventions     |
| S-03 | [03-documentation.md](./03-documentation.md)       | Doc structure, co-location, ADRs, No God Docs rule          |

---

## Guiding Principles

These principles underpin every sub-standard:

1. **No God Files** — God files (a single file owning many unrelated
   responsibilities) are **not allowed, ever** — this rule is absolute. The size
   *target* is a **recommended ≤ 200 lines** (per S-02 §3): a soft guideline with
   room to breathe and a review trigger, **not** a hard cutoff. A deliberately
   longer file is acceptable when splitting would fracture genuinely cohesive
   logic, provided the reason is noted at the top. Split by responsibility, not by
   line count — cohesion is the goal; the line count is only its proxy.
2. **Modularity First** — Loose coupling, high cohesion. Every module has a
   clear boundary and a defined public API.
3. **Referral Over Repetition** — Link to the authoritative source; never
   duplicate rules across files.
4. **Cognitive Load Management** — Optimize for human readability. Small,
   focused files reduce context-switching overhead.

---

## Version

| Field       | Value      |
| ----------- | ---------- |
| Version     | 1.1.0      |
| Created     | 2026-02-25 |
| Last Review | 2026-06-04 |
