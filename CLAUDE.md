# NARS-JARVIS — AI Agent Instructions

> A local-first cognitive system that pairs a Non-Axiomatic Reasoning System (NARS / ONA)
> as a persistent symbolic brain with an LLM as its language layer. This file sets the
> ground rules for all work in this project. **Read it before starting any task.**

> **MANDATORY:** Before any architectural, refactoring, or feature task, read
> [`standards/00-manifest.md`](standards/00-manifest.md) first — it routes you to the
> relevant sub-standards. The Standards (`standards/`) are part of these instructions and
> are binding. Per the manifest: **do not fabricate rules — if a rule is not defined, ask.**

---

## Project

We are building a local cognitive assistant on this machine, combining:

- **NARS (ONA)** — the persistent, explainable symbolic brain: reasoning, online learning, and goal-driven decisions. Written in C, runs locally and bounded.
- **An LLM** (Grok via xAI, or a local model) — the language layer: natural-language ↔ Narsese translation, narration, and investigation.
- **NARS-GPT** — the integration spine wiring the LLM to ONA (persistent memory, grounding, truth maintenance). Written in Python.

Target capabilities:

1. An assistant that **learns your habits** over time, rather than following scripts.
2. A **local sentinel with curiosity** — predicts the machine's normal behavior and flags surprises.
3. **"Programming in English"** into a permanent symbolic brain.
4. A **two-brain hallucination check** — NARS grounds the LLM against accumulated evidence.

Reference components live in sibling folders: `OpenNARS-for-Applications/`, `NARS-GPT/`, `OmniGlass/`.

> **Stack:** this project is built in **C** (ONA / NARS), **Python** (NARS-GPT and
> orchestration), and optionally **Rust + Node** (if the OmniGlass execution layer is
> reused). The Standards and their conventions are native to this stack; see
> [ADR-001](docs/adrs/ADR-001-adopt-and-adapt-engineering-standards.md) for the
> per-language module conventions.

---

## Development Principles

> These are the ground rules that govern all work in this project. The list grows as we
> establish more rules — and per Principle 1, it grows *alongside* the code, never after.

1. **Continuous, Modular Documentation** — Maintain high-quality, modular documentation alongside code throughout the entire development lifecycle, rather than waiting for a specific phase transition. Documentation is written and updated as part of the same change as the code it describes; it is never deferred to a later "documentation phase." This prevents knowledge silos and keeps the project's single source of truth current at every commit, so the codebase stays self-describing for any developer at any stage of the lifecycle.

2. **Modular Architecture by Default** — Build every component as a cohesive, loosely-coupled module with a clear boundary and a well-defined public interface, hiding its internal design decisions behind that interface (information hiding). This is the project's primary architectural rule, not a preference. Modular decomposition is the *foundational* practice in software design — established by Parnas's information hiding (1972) and operationalized as separation of concerns, high cohesion, and low coupling — because it is what lets us change one part without breaking the rest, test parts in isolation, and add capability by adding modules rather than editing working code. Apply it concretely through the Standards: modular, domain-based decomposition (S-01) and SOLID + Functional Core / Imperative Shell (S-02). See those files for the rules; this principle does not restate them.

3. **No God Files (absolute) — size limit is recommended, not hard** — A file may never become a "god file": one that owns many unrelated responsibilities. That rule is absolute. The size *target* (recommended ≤ 200 lines, per S-02 §3) is a soft guideline with room to breathe and a review trigger — not a hard cutoff; a deliberately longer file is acceptable when splitting would fracture genuinely cohesive logic, provided the reason is noted at the top. Cohesion is the real goal; line count is only its proxy. When a file starts accreting unrelated responsibilities, split it along a natural boundary.

4. **Simplicity First — KISS / YAGNI / Minimal Abstraction (binding)** — Solve the immediate problem with the simplest thing that works; never build for unstated future requirements. This governs all code, architecture, and refactoring.
   - **KISS** — Choose the most straightforward, linear, readable solution available.
   - **YAGNI** — Do not build features, abstractions, hooks, generalizations, or "future-proofing" for hypothetical use cases. Solve only what is asked.
   - **Minimal Abstraction** — Prefer simple functions and inline code over new classes, interfaces, or design patterns (Factory/Observer/etc.) unless the immediate task strictly mandates them.
   - **No premature DRYing** — Do not extract code into a shared file or generic utility until it is *actually* reused in **≥ 3** places. Two call sites is not enough.
   - **Dependency minimization** — Do not add a third-party library for a task solvable in a few lines of standard/native code.
   - **Explicit justification** — When introducing any new structural element (folder, module, class, pattern), state in the change *why it is the simplest option available*, not merely that it is clean.
   - **Architectural pause** — If a task seems to require a complex architectural shift, **stop and propose a simpler alternative to the user first** (an `EnterPlanMode`/`AskUserQuestion` moment), rather than building the complex version.
   - **Before writing or modifying code, verify:** (a) does this directly and *exclusively* solve the immediate problem? (b) is there a way with fewer lines, files, or new concepts? (c) have I avoided all "just in case" logic and defensive abstractions?
   - This principle is a *check on* Principles 2–3, not a contradiction: modular decomposition and splitting god files are still required — but the trigger is *demonstrated* responsibility/reuse, never anticipated. When in doubt, do less.
