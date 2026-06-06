# S-02 — Coding Practices

> **Scope**: SOLID principles, Functional Core / Imperative Shell, file-size limits,
> naming conventions, and general code-quality rules for this project's
> C / Python / (Rust) stack.

---

## 1. SOLID Principles

### 1.1 Single Responsibility (SRP)
Every module or function has **one reason to change**. If a function parses input AND calls
the reasoner AND formats output, it has three responsibilities and must be split.

### 1.2 Open/Closed (OCP)
Open for extension, closed for modification. Favor composition and configuration over
editing working code — e.g. add a new sensor channel as a new module, don't edit the core.

### 1.3 Liskov Substitution (LSP)
Implementations of an interface must honor its full contract and be substitutable for it
without breaking callers.

### 1.4 Interface Segregation (ISP)
No caller should depend on operations it does not use. Prefer small, focused interfaces over
large catch-all ones.

### 1.5 Dependency Inversion (DIP)
High-level logic must not depend on low-level details. Both depend on **abstractions**.
Inject dependencies (e.g. pass in the LLM client and the NARS handle) rather than
hard-coding them.

---

## 2. Functional Core, Imperative Shell

### What It Means
- **Functional Core** — pure functions: input in, output out, no side effects. All logic,
  truth calculations, and transformations live here. Trivially testable.
- **Imperative Shell** — the thin outer layer that does I/O: subprocess calls to ONA, LLM
  API requests, file reads, event sources. It orchestrates the functional core.

### Why
- Pure functions are deterministic and testable without mocks.
- Side effects are isolated to the shell, so bugs are easier to trace.
- Logic survives dependency swaps (e.g. changing LLM provider) because it has no I/O deps.

### Example (Python)
```python
# Functional Core — pure, testable
def revise(f1: float, c1: float, f2: float, c2: float) -> tuple[float, float]:
    w1, w2 = c1 / (1 - c1), c2 / (1 - c2)
    w = w1 + w2
    return (w1 * f1 + w2 * f2) / w, w / (w + 1)

# Imperative Shell — orchestrates I/O
def handle_observation(event: str, nar, llm) -> None:
    narsese = llm.to_narsese(event)     # I/O
    nar.add_input(narsese)              # I/O
```

---

## 3. File-Size Limits

| Asset Type    | Recommended | Rationale                          |
| ------------- | ----------- | ---------------------------------- |
| Source file   | ≤ 200       | Cognitive load; fits in one screen |
| Documentation | ≤ 200       | Readability; focused scope         |
| Test file     | ≤ 200       | One behavior cluster per file      |
| Config file   | ≤ 100       | Minimal and declarative            |

**These are recommended targets and review triggers — not hard cutoffs.** What is absolute
is the **no-god-file rule** (a file must never own many unrelated responsibilities; see
[00-manifest.md](./00-manifest.md) §1). When a file approaches the target, that is the
signal to **extract a module along a natural boundary** — default to splitting.

But the line count is a **proxy for cohesion, not the goal.** Do **not** fracture genuinely
cohesive logic across files just to get under the number (that produces "sibling sprawl" —
opening five files to follow one flow, which is worse than one slightly-long cohesive file).
If a split would harm readability more than the length does, keep the file longer — and add
a one-line comment at the top noting why it deliberately exceeds the target, so reviewers
see a justified exception, not drift.

**Why we limit file size**: human working memory handles only a handful of distinct concepts
at once (Miller's Law). Long files force too much context, raising error rates and slowing
review.

---

## 4. Naming Conventions

### 4.1 General Rules
- **Be descriptive.** `calculate_surprise` over `cs`.
- **No abbreviations** unless universally understood (`id`, `url`, `api`).
- **Booleans** read as predicates: `is_grounded`, `has_belief`, `should_act`, `can_execute`.

### 4.2 Per-Language Casing (follow each language's idiom)
| Construct           | Python (PEP 8)     | C (ONA convention)            | Rust               |
| ------------------- | ------------------ | ----------------------------- | ------------------ |
| Variables/functions | `snake_case`       | `snake_case`                  | `snake_case`       |
| Constants           | `UPPER_SNAKE_CASE` | `UPPER_SNAKE_CASE`            | `UPPER_SNAKE_CASE` |
| Types / structs     | `PascalCase`       | `PascalCase` (typedef)        | `PascalCase`       |
| Modules / files     | `snake_case.py`    | `PascalCase.{c,h}` (as in ONA) | `snake_case.rs`    |
| Directories         | `snake_case`       | `snake_case`                  | `snake_case`       |

When extending existing code (e.g. ONA's C), match the surrounding file's conventions.

### 4.3 Function Naming
- **Transformers**: verb + noun — `format_answer`, `parse_narsese`.
- **Predicates**: `is_/has_/can_` + description — `is_form_valid`.
- **I/O orchestrators** (shell): name by the action — `handle_observation`, `send_to_ona`.

---

## 5. Error Handling
- Handle errors at **system boundaries** (user input, LLM/API responses, subprocess I/O,
  file reads).
- Do not wrap internal calls in try/except unless there is a specific recovery strategy.
- Use typed/structured errors, not raw strings.
- Log errors with context (operation, inputs).

---

## 6. Testing Expectations
- **Unit tests** cover the functional core (pure functions, truth math, parsing).
- **Integration tests** cover the imperative shell (ONA subprocess, LLM calls, event flow).
- Tests live adjacent to the code they test (e.g. `test_memory.py` beside `memory.py`;
  ONA keeps its C tests under `unit_tests/` and `system_tests/`).

---

*Parent: [00-manifest.md](./00-manifest.md)*
