# NARS-JARVIS

A local-first, learning, explainable cognitive assistant pairing a Non-Axiomatic Reasoning
System (NARS / ONA) with an LLM. **The single source of truth for scope is
[docs/prd/PRD.md](docs/prd/PRD.md).** Engineering rules: [CLAUDE.md](CLAUDE.md) +
[standards/](standards/). Binding technical contracts:
[ADR-001](docs/adrs/ADR-001-adopt-and-adapt-engineering-standards.md),
[ADR-002](docs/adrs/ADR-002-execution-safety-and-trigger-soundness.md).

## Status
**M0 & M1 verified (live GGUF). M2 Code-Complete. M3 Phase A complete; Phase B gated on the
OmniGlass sandbox audit.** ONA (L1), `language/` (GGUF-verified), `memory/` (L2 SQLite),
`contradiction/` (C2 guard), `sentinel/` (C3 observe-only), and `execution/` (closed typed catalog
+ autonomy predicate + mock executor; `OmniGlassExecutor` scaffolded but **not wired**) are built
and verified — **23 test suites**, capstones `src/test_m0.py` / `test_m1.py` / `test_m2.py`. The
system is a complete, safe **observe-reason-propose** assistant. Pending: live `psutil`/`watchdog`
provisioning, dedup tuning (R1), and — before any live action — a **passed adversarial OmniGlass
sandbox audit**.

## Repository layout
```
CLAUDE.md            # AI-agent instructions + development principles
standards/           # Engineering standards (S-00..S-03)
docs/
  prd/PRD.md         # SINGLE SOURCE OF TRUTH (product scope)
  prd/PRODUCT-BRIEF.md
  adrs/              # Architecture Decision Records
src/
  brain/             # ONA reasoning-core wrapper (built, tested)
  language/          # LLM channel: GBNF translation + grounding (built; live LLM pending GGUF)
  memory/            # durable SQLite system-of-record (L2) + sync (built, tested)
  contradiction/     # C2 pre-commit contradiction guard (built, tested)
  jarvis.py          # application orchestrator (M0 C1 loop + M1 C2 guard)
  sentinel/          # C3 observe-only: discretizer + surprise + narration (built, tested)
  execution/         # C4 constraint: closed catalog + autonomy predicate + executors (Phase A; Phase B gated)
  shared/            # cross-cutting utilities (atom sanitizer)
OpenNARS-for-Applications/  # ONA (C reasoner) — vendored upstream; build with build.sh
NARS-GPT/                   # integration-spine reference   — vendored upstream
OmniGlass/                  # sandboxed execution engine     — vendored upstream
```

## Build & verify what exists today
```sh
# 1. Build the reasoner (needs Xcode Command Line Tools / clang)
(cd OpenNARS-for-Applications && sh build.sh)

# 2. Run the brain tests
cd src && python3 -m brain.test_parse && python3 -m brain.test_ona_integration
```

## Architecture
Domain modules are cohesive and loosely coupled (CLAUDE.md Principle 2; standards/01); each
exposes one public interface via its `__init__.py`. See per-module READMEs.
