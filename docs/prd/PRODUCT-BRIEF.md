# Product Brief — NARS-JARVIS

> **Editorial note.** Captured as authored by the reviewing Principal PM / Staff Engineer
> after the Phase-1/2 architecture inquiry. This is the **narrative foundation**; the
> canonical, engineering-authoritative spec is [PRD.md](PRD.md), and the binding safety
> contracts are [ADR-001](../adrs/ADR-001-adopt-and-adapt-engineering-standards.md) and
> [ADR-002](../adrs/ADR-002-execution-safety-and-trigger-soundness.md). Two claims below are
> deliberately **tempered in the PRD for accuracy** (see PRD §8 NFR-5): "absolute
> explainability" applies to the reasoning, memory, and execution layers but **not** the LLM
> translation step — the single acknowledged non-auditable link; and "categorically safer
> than vector retrieval" is the project's **hypothesis under test at M0**, not a proven result.

---

## 1. Executive Summary (The "Why")
The current generation of large language models offers unprecedented conversational fluency
but suffers from fundamental architectural flaws when deployed as persistent personal agents:
they are stateless, their memories are probabilistic and non-auditable, and their execution
pathways are prone to dangerous hallucinations. NARS-JARVIS is a local-first, learning
cognitive assistant that solves this by bridging the probabilistic language capabilities of
an LLM with the deterministic, explainable reasoning of a Non-Axiomatic Reasoning System
(NARS). By explicitly separating the language center from the execution and memory centers,
we are building an assistant that accumulates durable knowledge, flags contradictions
logically rather than probabilistically, and earns autonomy through human-in-the-loop habit
learning. This is an experimental, hypothesis-driven pursuit to prove that symbolic,
auditable evidence trails provide a categorically safer and more reliable foundation for
local machine autonomy than vector-based retrieval.

## 2. Problem Statement
Power users automating their digital lives currently rely on LLMs paired with vector
databases and cron jobs. This "simple stack" fails silently. Vector databases retrieve
memories by semantic proximity, so logically related but lexically distant facts (e.g. a
"penicillin allergy" and an "amoxicillin prescription") may never enter the LLM's context
window simultaneously, letting the system confidently agree with contradictory statements.
Existing LLM agents also lack an auditable chain of evidence for their actions; when they err,
the user cannot inspect the exact logic tree that led to the failure. Users pay the invisible
costs of unreliability, silent data corruption, and the anxiety of giving open-ended
generative models access to their local file systems.

## 3. Solution & Vision
A deeply personalized, local-first operating-system sentinel and assistant. NARS-JARVIS uses
the open-source NARS-GPT project as an integration spine, relying on an LLM for natural
language translation while maintaining a strictly bounded C-based reasoning engine (ONA) to
process logic. The long-term vision is a local agent that understands the user's routines,
anticipates needs via temporal induction, and executes sandboxed commands with traceability.
It does not replace the LLM; it governs it — acting as an auditable memory layer and an
execution gateway where autonomy is earned per action, not granted by default.

## 4. Value Proposition & Differentiation
The core differentiator is explainability tied to strict deterministic execution. Unlike
agents that generate code on the fly, this system cannot invent new actions: execution is
constrained to a pre-vetted catalog of typed, parameterized commands. Memory is not a
black-box embedding space; it is a two-tier architecture where a permanent SQLite-backed
system-of-record feeds a bounded, self-forgetting reasoning cache. When the system flags an
anomaly or proposes an action, it provides a specific, verifiable evidence trail. The
structural guarantee — that hallucinations cannot trigger destructive shell execution, and
that memory conflicts are resolved via formal logic — is the defensible moat against the
stochastic unreliability of standard agents.

## 5. Target Audience & Personas
The primary user is the "Owner" — a highly technical power user who demands absolute privacy,
low-latency local performance, and granular OS control. They are familiar with software
architecture, comfortable compiling C and Rust binaries, and refuse to grant cloud LLMs
unmonitored access to their local environment. They want not a chatty companion but an exact,
reliable, rigorously auditable digital proxy. Enterprise / multi-tenant deployments are
explicitly out of scope for this foundational phase.

## 6. Core User Journeys & "Jobs-to-be-Done"
The user hires NARS-JARVIS as an infallible auxiliary memory and a vigilant local observer.
First, the user teaches the system facts and rules in plain English, expecting retrieval weeks
later with a certainty value and citation. As the user works, the system ingests
edge-triggered, discretized telemetry (CPU, specific filesystem changes), building temporal
correlations. When the user repeatedly performs a sequence — e.g. opening a communication app
after reviewing morning metrics — the system proposes automating it. The critical journey is
**rejection**: a declined proposal acts as a negative-evidence training signal, eroding the
system's confidence in that correlation until it aligns with the user's actual intent.

## 7. Scope Definition (In / Out)
**In scope:** a two-tier memory (SQLite persistent store + ONA bounded cache); LLM-driven
translation of English into constrained Narsese schemas via embedding-gated deduplication; a
local sentinel using edge-triggered emission and discretized telemetry; sandboxed execution
via OmniGlass, restricted to a pre-authored catalog of parameterized commands.
**Out of scope:** any generative script execution (e.g. `^run_shell(string)`); unsupervised
autonomy at launch; motor babbling / random exploratory execution (disabled via policy
overlay); semantic drift across arbitrary concept renames without explicit user mapping;
cloud-hosted processing, aside from the optional external API for the LLM translation layer.

## 8. Success Metrics & KPIs
Gated by a strict **"real-pain-or-kill"** metric at M0: if the user cannot produce three
documented instances where a standard vector database failed to detect a contradiction that
NARS successfully flags, the project is terminated. Assuming survival:
- **Grounding accuracy** — % of NL inputs mapped to correct existing Narsese concepts without
  redundant atoms.
- **Sentinel signal-to-noise** — anomaly true-positive vs false-positive rate (requires
  aggressive discretization-threshold tuning).
- **Autonomy conversion** — number of temporal correlations that graduate from the
  human-confirmation gate to autonomy, with **zero sandbox escapes** during execution.

## 9. Key Assumptions, Risks, and Dependencies
The existential risk is NL→Narsese grounding reliability. We assume a constrained LLM schema
plus an embedding-similarity threshold will prevent the reasoner from ingesting syntactically
correct but semantically absurd data. Because NARS cannot inherently detect semantic
absurdity, the human-in-the-loop contradiction check (C2) is a load-bearing dependency. A
secondary critical risk is execution safety: NARS-JARVIS depends entirely on the structural
integrity of the OmniGlass sandbox — a sandbox escape collapses the execution guarantee. We
also acknowledge NARS's inability to distinguish correlation from causation; the system
assumes the human confirmation loop provides the negative reinforcement to correct confounded
patterns over time.

## 10. Preliminary Technical Considerations
A disciplined separation of concerns across C, Python, and the execution layer. ONA is treated
purely as a Level-1 reasoning cache, with truth maintenance and long-term pinning handled by a
durable external system of record. The sentinel pipeline strictly enforces edge-triggered
emission, symbol discretization, and debouncing — feeding raw OS events into the 40-slot
attention buffer causes catastrophic failure. The execution layer is governed by a strict,
type-enforced contract: operations registered in the C core accept only pre-defined enums or
grounded atoms mapped to immutable command templates, under a policy wrapper that overrides
NARS's native RL defaults (exploratory execution set to zero, high-confidence observation
floors before any proposal). See ADR-002 for the binding contract.
