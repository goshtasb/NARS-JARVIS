# ADR-001: Modular Architecture and Engineering-Standards Decisions

## Status
Accepted

## Context
This project (NARS-JARVIS) is built on a mixed stack — **C** (ONA / NARS), **Python**
(NARS-GPT and orchestration), and optionally **Rust + Node** (if the OmniGlass execution
layer is reused). Two foundational decisions needed to be recorded so the Standards
(`standards/00-manifest.md`–`03-documentation.md`) apply consistently across all languages:

1. How a module exposes its **public interface**, when "the public surface" means different
   things in C, Python, and Rust.
2. How to frame the **file-size limit** — the project owner directed that it be a
   recommendation with room to breathe, while **god files remain absolutely disallowed**,
   and that the architectural stance be grounded in researched best practice.

## Decision
1. **Modular, domain-based decomposition is the primary architectural rule** (CLAUDE.md
   Principle 2; S-01). The system is organized into cohesive, loosely-coupled domain modules
   (e.g. `brain/`, `language/`, `memory/`, `sentinel/`), each hiding its internals behind one
   public interface.
2. **Public interface, per language:**
   - **Python** — the package `__init__.py` with an explicit `__all__`; other files private.
   - **C** — the module's `.h` header; the `.c` implementation private.
   - **Rust** — `pub` items in `mod.rs` / `lib.rs`; non-`pub` items private.
   Naming follows each language's idiom (PEP 8 `snake_case` for Python; existing ONA
   conventions for C), keeping S-02's intent: descriptive, no cryptic abbreviations, boolean
   `is_/has_/can_` prefixes.
3. **File-size rule:** a **recommended ≤ 200 lines** (soft target and review trigger, per
   S-02 §3), with deliberate exceptions allowed when splitting would fracture cohesive logic
   — annotated with a one-line reason at the top of the file. **God files are not allowed
   under any circumstances**; the no-god-file rule is absolute and independent of line count.

## Consequences
- One consistent architecture and size philosophy across C, Python, and Rust.
- Every new module must declare an explicit public interface in its language's idiom.
- The reasoning core (`brain/`) stays free of I/O dependencies, keeping logic testable in
  isolation (S-02 Functional Core / Imperative Shell).
- Slight overhead: contributors read `00-manifest.md` and apply the per-language conventions
  above; this ADR is the canonical reference.

## Alternatives Considered
- **A single hard line ceiling (e.g. 200 or 300).** Rejected: a hard cutoff fractures
  cohesive logic into "sibling sprawl"; cohesion — enforced by the absolute no-god-file rule
  — is the real goal, and line count is only a proxy.
- **One uniform public-interface convention for all languages.** Rejected: C, Python, and
  Rust express module boundaries differently; forcing one idiom onto all three is awkward
  and unidiomatic. Per-language conventions honor the same principle (information hiding).

## Basis (researched best practice)
Modular decomposition is not merely *a* good practice — it is the foundational one in
software design, tracing to David Parnas's *information hiding* (1972) and operationalized
today as **separation of concerns, high cohesion, and low coupling**. Current consensus
(including microservice boundary design) restates the same goal: each module does one thing
well and changes independently, so you can "change one thing without breaking everything
else." This project's modularity-first stance is grounded in that consensus, not assertion.
