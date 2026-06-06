# S-01 — Architecture Standards

> **Scope**: Module boundaries, modular decomposition, domain isolation, and public
> interface contracts for this project's C / Python / (Rust) stack.
>
> See [ADR-001](../docs/adrs/ADR-001-adopt-and-adapt-engineering-standards.md) for the
> architectural decisions and per-language conventions this standard applies.

---

## 1. Architectural Pattern: Modular, Domain-Based Decomposition

### What It Is

The system is decomposed into **cohesive domain modules**, organized by capability, not by
technical layer. Each module owns everything it needs — logic, data access, and its own
public interface — and hides its internal design decisions behind that interface
(information hiding).

The project's domains follow the capabilities in `CLAUDE.md`, e.g.:

```
brain/        # NARS reasoning / learning / decisions (wraps ONA)
language/     # LLM channel: natural language <-> Narsese translation
memory/       # persistent symbolic memory, grounding, truth maintenance
sentinel/     # local event observation + surprise detection
shared/        # cross-cutting infrastructure (subprocess, config, logging)
```

### Why We Use It

- **Change isolation** — a change inside one module does not cascade to unrelated ones.
- **Parallel work** — modules can be developed and tested independently.
- **Easy deletion / replacement** — removing a capability means removing one module folder,
  not hunting across layer-based directories.
- **Cognitive locality** — everything needed to understand a capability lives in one place.

---

## 2. Module Boundary Rules

### Rule 2.1 — Self-Contained Modules

Every module must contain all the code it needs to do its job: logic, data access, and
types. Genuinely cross-cutting infrastructure (subprocess wrappers, config, logging) lives
in a dedicated `shared/` module — never inside a domain module.

### Rule 2.2 — No Cross-Module Internal Imports

A module must not reach into another module's internal files. All inter-module use goes
through the target module's **public interface** (Rule 2.3) or a shared event / state layer
in `shared/`.

### Rule 2.3 — Explicit Public Interface

Each module exposes exactly one public surface, in its language's idiom; internals stay
private:

| Language | Public surface                                   | Private                       |
| -------- | ------------------------------------------------ | ----------------------------- |
| Python   | the package `__init__.py` with explicit `__all__` | other files in the package    |
| C        | the module's `.h` header                          | the `.c` implementation       |
| Rust     | `pub` items in `mod.rs` / `lib.rs`                | non-`pub` items               |

**Bad (Python) — reaching into internals:**
```python
from brain.ona_subprocess import _raw_send   # private internal
```

**Good — through the public interface:**
```python
from brain import add_belief, ask
```

---

## 3. Shared Infrastructure

Code reused across **three or more** modules belongs in `shared/` (subprocess wrappers,
config, logging, common types).

**Promotion Rule**: code starts inside a module and only moves to `shared/` when a third
module needs it. Premature abstraction creates coupling, not reuse.

---

## 4. Dependency Direction

Dependencies flow **inward** toward `shared/`, never laterally between domain modules:

```
brain/     -->  shared/
language/  -->  shared/
brain/     -/-> language/   (PROHIBITED — go through public interfaces / events)
```

The reasoning core (`brain/`) must not depend on the language or I/O layers; those depend
on it. This keeps the pure reasoning/logic testable in isolation (see S-02 Functional Core).

---

## 5. Enforcement

- Boundary violations are caught in code review: every new cross-module import must target
  a public interface, never an internal file.
- Where the language allows, encode the boundary in tooling (e.g. Python import-linter layer
  contracts, Rust module visibility) so CI flags violations automatically.

---

*Parent: [00-manifest.md](./00-manifest.md)*
