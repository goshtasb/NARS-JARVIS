# PRD — NARS-JARVIS: A Local, Learning, Explainable Cognitive Assistant

> **Note on length (S-02 §3 / S-03 §1):** this file deliberately exceeds the 200-line
> recommendation. A PRD is one cohesive artifact reviewed end-to-end; splitting it would
> fracture that cohesion ("sibling sprawl"). It keeps a single responsibility — define the
> product. Detailed safety contracts are referred out to ADR-001/ADR-002 (Referral Over
> Repetition); the narrative foundation is [PRODUCT-BRIEF.md](PRODUCT-BRIEF.md).
>
> **This PRD is the FINAL, single source of truth for product scope. Build to it.** ADR-001/002
> are the binding technical contracts it references; the brief is subordinate to this document.

| Field      | Value                                                              |
| ---------- | ------------------------------------------------------------------ |
| Status     | **FINAL — single source of truth (build to this)**                 |
| Version    | 1.0.0                                                              |
| Date       | 2026-06-04                                                         |
| Owner      | Project owner                                                      |
| Reviewers  | Principal PM, Principal Engineer                                   |
| Related    | `PRODUCT-BRIEF.md`, `CLAUDE.md`, `standards/`, `ADR-001`, `ADR-002` |

---

## 1. TL;DR

**NARS-JARVIS** is a **local-first personal cognitive assistant** pairing a Non-Axiomatic
Reasoning System (NARS, via the C engine **ONA**) as a **persistent, explainable symbolic
brain** with a **large language model** (Grok via xAI, or a local model) as its **language
layer**. The LLM translates between natural language and the reasoner's formal language
(Narsese) and narrates; NARS accumulates knowledge, learns the user's patterns online, checks
the LLM against accumulated evidence, and makes goal-driven decisions — all **locally**, with a
**human in the loop for any action**. The integration spine is a fork of the open-source
**NARS-GPT** project.

**This is an experimental, hypothesis-driven system.** The central risk is NL ↔ Narsese
**grounding** reliability. We de-risk by shipping in **four phases**, each independently useful;
M0 is gated by a hard **"real-pain-or-kill"** test.

---

## 2. Problem & Motivation

LLM assistants are fluent but **stateless and fuzzy**: they do not durably learn *you*; their
memory is non-auditable; they hallucinate. Symbolic reasoners are **persistent, explainable,
adaptive** but cannot understand language or perceive. NARS-JARVIS pairs them so each covers the
other's failure mode.

The concrete failure of the "simple stack" (LLM + vector DB + cron): vector retrieval is by
**semantic proximity**, so logically related but lexically distant facts — a "penicillin
allergy" and an "amoxicillin prescription" — may never co-enter the context window, letting the
system confidently agree with a contradiction. NARS resolves this **structurally** (via the
bridging fact `amoxicillin → penicillin`) with an evidence trail, independent of retrieval luck.

**Honest framing (see R8):** the simple stack covers the easy 80%. NARS's differentiated value
is (a) **emergent** learning of patterns nobody programmed, (b) a **symbolic, auditable**
evidence trail per belief/action, and (c) **explainable adaptive control**. M0/M2 exist to
*prove or disprove* that value — not assume it.

---

## 3. Goals & Non-Goals

### Goals
- **G1** — A permanent symbolic memory the user can **program in plain English** (durable
  system-of-record; see §6 two-tier memory).
- **G2** — **Explainable** answers: every conclusion cites supporting evidence + a certainty
  value.
- **G3** — A **local sentinel** that learns normal behavior and flags anomalies.
- **G4** — **Habit learning** that suggests, and (once trusted) performs, routine actions.
- **G5** — A **hallucination grounding check**: NARS flags LLM claims that contradict evidence.
- **G6** — **Local-first and private by default**.
- **G7** — **Human-in-the-loop safety** for any action that touches the system.

### Non-Goals (explicit)
- ❌ Trading / financial decisions (assessed, rejected — poor fit, high risk).
- ❌ Full unsupervised autonomy at launch; autonomy is earned per-action, gated (ADR-002).
- ❌ Perception/vision/speech-understanding; relies on external channels (LLM, OCR, STT).
- ❌ Cloud-hosted product; no server component.
- ❌ Replacing the LLM's general intelligence; NARS is the *learner*, not the language brain.
- ❌ Generative script execution (e.g. `^run_shell(string)`) — forbidden by ADR-002.
- ❌ Motor babbling / random exploratory execution — disabled by policy (ADR-002).
- ❌ Multi-user / enterprise at this stage.

---

## 4. Users & Personas

- **Primary — "The Owner":** a technical power-user on macOS who values privacy,
  explainability, and a tool that adapts to them; comfortable compiling C/Rust; refuses to give
  cloud LLMs unmonitored local access. Wants an exact, auditable digital proxy — not a chatty
  companion.
- **Secondary (future):** other individuals running their own local instance. No multi-tenant.

---

## 5. Product Scope — The Four Capabilities

| ID | Capability | Representative user story | Primary component(s) | Phase |
| -- | ---------- | ------------------------- | -------------------- | ----- |
| **C1** | Program-in-English permanent brain | "I tell it facts in English; weeks later it answers with a certainty value and cites the memory item." | LLM + NARS-GPT spine + ONA + memory | **M0** |
| **C2** | Two-brain hallucination check | "When the LLM contradicts what I've told it, it flags the conflict instead of agreeing." | NARS evidence base + check loop | M1 |
| **C3** | Curious local sentinel | "It learns my machine's rhythm and says 'CPU pegged at 2am, unusual — want me to look?'" | sentinel pipeline + NARS surprise + LLM narration | M2 |
| **C4** | Learns-you assistant | "It notices my weekday-morning routine and offers to do it; after enough confirmations, it does." | sentinel + NARS procedural learning + sandboxed execution | M3 |

---

## 6. System Architecture

```
   English / voice in ─▶ LLM (Grok via xAI, or local llama.cpp model)
                         = mouth / translator / investigator
                              │ constrained-schema Narsese   ▲ answers / contradictions / narration
                              ▼                               │
                         NARS-GPT spine (Python): grounding, embedding dedup,
                         attention buffer, truth maintenance
                              │ *commands                     ▲ derivations / answers
                              ▼                               │
                         ONA / NARS (C): bounded, local, deterministic reasoner  ◀── L1 cache
                              ▲                               │ load / evict
        local events ────────┘  (sentinel pipeline → Narsese) │
                                                     SQLite system-of-record (durable, pinnable)
```

**Two-tier memory (resolves the "permanent vs self-forgetting" question).** The **system of
record** is a durable, **pinnable** store (SQLite target; NARS-GPT ships a JSON store we will
harden) — this is what G1 means by "permanent." **ONA is an L1 reasoning cache**: bounded
(`CONCEPTS_MAX = 4096`) and self-forgetting (usefulness-ranked eviction). Evicted concepts are
**not lost** — they are re-loaded into ONA's attention buffer from the system-of-record when a
query makes them relevant. Core user facts are **pinned** and never evicted from the durable store.

**Module map** (`CLAUDE.md` Principle 2; `standards/01`; per-language interfaces in ADR-001):
`brain/` (ONA wrapper), `language/` (LLM channel), `memory/` (system-of-record + grounding),
`sentinel/` (observation + surprise), `shared/` (subprocess, config, logging).

**Reuse, don't rebuild:** **NARS-GPT** (~900 lines Python) is the forked spine, LLM backend
repointed to Grok or local; **OmniGlass** (sandboxed execution: `sandbox-exec`, confirmation,
redaction) is the C4 execution gateway (ADR-002).

---

## 7. Functional Requirements

- **FR-1 (C1):** Accept English statements/questions; translate via a **constrained Narsese
  schema** (structured claims, not free-form generation); persist across restarts; answer from
  memory with a frequency/confidence value and a citation.
- **FR-2 (C1):** Deduplicate near-synonymous terms via embedding similarity (grounding).
- **FR-3 (C2):** For any LLM-asserted claim, query NARS; if a contradicting belief exists above
  threshold, surface the conflict and both sides' evidence. **C2 is load-bearing** — NARS cannot
  detect semantic absurdity at ingestion (see R1).
- **FR-4 (C3):** The sentinel ingests local signals through a strict pipeline — **sampling →
  symbolic discretization → edge-triggered emission → debounce/aggregate → allow-list → event
  budget** — before Narsese translation. Raw OS events must never reach ONA's 40-slot buffer.
- **FR-5 (C3):** Detect prediction failures ("surprise") above a threshold and trigger the LLM
  to investigate/narrate. **C3 never auto-acts.**
- **FR-6 (C4):** Learn recurring temporal/procedural patterns and propose the action.
- **FR-7 (C4):** Execute **only** via the pre-authored, typed operation catalog (ADR-002), via
  the sandboxed executor, and **only** after confirmation until per-action autonomy is earned.
- **FR-8 (C4):** Apply the **autonomy-eligibility predicate** (ADR-002): babbling off; high
  confidence + frequency floors; minimum observation + confirmation counts.
- **FR-9 (all):** Every belief, prediction, and decision is inspectable with its evidence trail.

---

## 8. Non-Functional Requirements

- **NFR-1 Local-first:** all reasoning, memory, learning run on-device; the only optional remote
  call is the LLM (if cloud Grok is chosen).
- **NFR-2 Privacy:** nothing leaves the machine except, optionally, redacted text to the chosen
  LLM. A fully-local LLM path must remain supported.
- **NFR-3 Latency:** NARS decisions are sub-millisecond locally; the LLM call is the bottleneck.
  The sentinel runs continuously **without** an LLM call except on surprise.
- **NFR-4 Reliability/Safety:** no action executes without confirmation (until earned);
  reversible-only for autonomy; all execution sandboxed (ADR-002).
- **NFR-5 Explainability:** the **reasoning, memory, and execution** layers are fully
  evidence-traceable. **Honest caveat:** the **LLM translation step (NL → Narsese) is the single
  non-auditable link** — a probabilistic black box. We constrain it (schema + embedding dedup)
  and *check its output* (C2 + human confirmation), but we do not claim the translation itself
  is explainable. Everything *downstream* of grounding is.
- **NFR-6 Memory durability:** the **system-of-record is durable and pinnable** (G1); the **ONA
  cache is bounded and self-forgetting** (§6). These are two tiers, not a contradiction.
- **NFR-7 Resource bounds:** runs within a single laptop's resources.
- **NFR-8 Portability:** macOS first; Linux/Windows later.

---

## 9. Key Design Decisions (with rationale)

1. **NARS is the persistent learner; the LLM is the language brain** — complementary, not
   redundant.
2. **LLM backend is swappable** (Grok cloud or local) behind one interface — trade privacy vs
   capability without re-architecting.
3. **Fork NARS-GPT** rather than rebuild the failure-prone bridge (grounding, memory, truth
   maintenance); MIT-licensed.
4. **Reuse OmniGlass's sandbox** for execution — the dangerous part is solved.
5. **Two-tier memory** — durable pinnable system-of-record + bounded ONA reasoning cache (§6).
6. **Human-in-the-loop by default; autonomy earned per-action** after demonstrated accuracy.
7. **Execution safety = catalog contract (ADR-002):** finite, typed, parameterized operations;
   **no generative operations ever**; the LLM is decoupled from the execution path.
8. **Trigger soundness = autonomy-eligibility predicate (ADR-002):** ONA's game-agent defaults
   (0.501 threshold, 0.2 babbling) are overridden; babbling off, high confidence/frequency
   floors, minimum observation + confirmation counts; human rejection is a negative-evidence
   correction signal.
9. **Modular, domain-based architecture** per `CLAUDE.md` / S-01 / ADR-001.

---

## 10. Milestones & Phased Plan

- **M0 — Foundation (C1).** Fork NARS-GPT; repoint LLM; decide embeddings source; deliver
  persistent English→Narsese memory with cited, certainty-valued answers. **Gate (real-pain):**
  the owner must produce **≥3 documented cases** where a vector DB missed a contradiction NARS
  catches — else the project is **terminated**. *Effort: days–2 weeks.*
  **Status (2026-06-05): VERIFIED (live GGUF).** Gate met (3 logs); deterministic spine + capstone
  (`src/test_m0.py`) verified, and live-validated on Llama-3-8B-Instruct-Q4 + nomic-embed-text:
  GBNF syntax robust, semantic fidelity solid for atomic claims. Known follow-ups: dedup-threshold
  tuning (R1) and an emit-array prompt for compound sentences.
- **M1 — Grounding check (C2).** Contradiction-detection loop on M0. **Exit:** flags ≥ a target %
  of scripted conflicts with evidence. *~1–2 weeks.*
  **Status (2026-06-05): VERIFIED (live GGUF).** Pre-commit polarity guard wired into
  `jarvis.learn`; direct-negation contradictions flagged with dual evidence trails and commit
  deferred to the human (`src/test_m1.py`). Scope: direct negations only (transitive don't
  propagate, per the verified `conf = c1·c2·f` limit).
- **M2 — Curious sentinel (C3).** Observation pipeline + surprise + narration; **observe-only.**
  **Exit:** useful flags on injected anomalies at an acceptable false-positive rate. *~2–4 weeks.*
  **Status (2026-06-05): Code-Complete; Pending Sensor Provisioning.** Schmitt discretizer
  (exactly-once, flap-immune), watchdog rollup (burst→one event), token-bucket backstop, surprise
  detection (prediction-divergence), and action-forbidden narration (deterministic sanitizer +
  fallback) — capstone `src/test_m2.py`. Pending live `psutil`/`watchdog` provisioning.
- **M3 — Learns-you assistant (C4).** Habit learning + suggestion; then gated, sandboxed
  execution of vetted low-risk actions. **Prerequisites:** passed OmniGlass sandbox audit;
  typed-catalog contract; autonomy predicate in force. *Multi-week, open-ended.*
  **Status (2026-06-05): Phase A complete (architecture of constraint).** Closed typed operation
  catalog (unregistered ops rejected + security-logged), autonomy predicate
  (`MOTOR_BABBLING_CHANCE=0` + confidence/frequency/observation/confirmation floors), MockExecutor
  with ONA reinforce/erode feedback. **Phase B (live execution) is scaffolded but NOT wired** — the
  `OmniGlassExecutor` refuses to run until `authorized=True` AND a `SandboxClient` is injected,
  blocked on a passed adversarial audit of the OmniGlass sandbox. See `src/execution/`.

*Effort figures carry real uncertainty, concentrated in grounding (M0/M1).*

---

## 11. Risks & Mitigations (the honest list)

| ID | Risk | Severity | Mitigation |
| -- | ---- | -------- | ---------- |
| **R1** | **NL↔Narsese grounding reliability** — the #1, existential risk. Includes two named failure modes: (a) **semantic absurdity** — NARS does *not* reject a plausible-but-false claim at ingestion; (b) **arbitrary rename** — embedding dedup does *not* merge embedding-distant names for the same entity. | High | Constrained-schema generation (not free-form); embedding dedup; a **measured grounding-accuracy harness** (incl. paraphrase, rename, absurd-assertion cases) as the M0 gate; **C2 + human confirmation are load-bearing**, not optional; arbitrary renames require an explicit identity mapping. |
| **R2** | Sentinel "surprise" is noisy | Med | Edge-triggered discretization + debounce + allow-list + event budget (FR-4); tune thresholds; measure FP rate in M2. |
| **R3** | Autonomous action causes harm / **OmniGlass sandbox escape** | High | Catalog contract + no generative ops (ADR-002); human-in-the-loop; reversible-only; **autonomy gated on a passed adversarial OmniGlass sandbox audit**; kill switch. |
| **R4** | Privacy regression if cloud Grok is used | Med | Support fully-local LLM; redact before send; explicit user choice. |
| **R5** | NARS learns a **confounded** correlation (correlation ≠ causation) | Med | Confidence/frequency floors + min observation count (ADR-002); human rejection feeds negative evidence and erodes bad patterns; suggestion-only until proven. |
| **R6** | Integration complexity across C / Python / (Rust) | Med | Clear module boundaries (ADR-001); Python-only through M2; add Rust/OmniGlass at M3. |
| **R7** | Research-grade dependencies (ONA, NARS-GPT) | Med | ONA is mature/stable C; NARS-GPT is ~900 lines we fork and own; both MIT. |
| **R8** | **Over-engineering** vs the simple stack | Med | M0/M2 are value-validation; if NARS's unique value doesn't show, reassess scope. |
| **R9** | Testing a non-deterministic learner | Med | Deterministic unit tests on the functional core; ONA is deterministic given a seed; fixed-input integration tests; behavioral evals on scripted scenarios. |

---

## 12. Success Metrics

- **C1:** grounding accuracy (% mapped to correct existing concept, no redundant atoms); answer
  correctness on a scripted recall set; persistence across restarts.
- **C2:** contradiction-detection recall/precision on a labeled conflict set.
- **C3:** anomaly true-positive vs false-positive rate on injected events.
- **C4:** routine-proposal precision; user acceptance rate; **zero sandbox escapes**; zero
  unsafe autonomous actions.
- **Overall:** every surfaced output carries a working evidence trail (binary, must hold).

---

## 13. Dependencies & Tooling

- **Languages:** C (clang) + Python 3.10+ mandatory; Rust + Node only if OmniGlass execution is
  used (M3).
- **Base:** Homebrew, Xcode Command Line Tools.
- **Python:** modern `openai` SDK (xAI base URL for Grok), `numpy`, `scipy`, `scikit-learn`,
  `nltk` (+ WordNet); `psutil`, `watchdog` (sentinel); SQLite (stdlib) for the system-of-record.
- **LLM:** xAI Grok key **or** local `llama.cpp` + model. Embeddings source must be decided
  (OpenAI vs local) — see Open Questions.
- **Voice (optional, M3):** `whisper.cpp`/`openai-whisper` (STT), macOS `say`/Piper (TTS).

---

## 14. Open Questions for PM & Engineering

1. ~~Cloud Grok vs fully-local LLM — default?~~ **RESOLVED: fully-local (llama.cpp), GBNF-constrained** (NFR-1/2).
2. ~~Embeddings source if not OpenAI — local model OK?~~ **RESOLVED: local (nomic-embed-text via llama.cpp).**
3. **Autonomy ceiling** — which action classes may ever run without confirmation?
4. **Target hardware** — this laptop only, or a spec to fit (local-LLM RAM/GPU)?
5. **MVP definition** — is M0 (C1) alone shippable, or is M1 required?
6. **Single-user only**, or design now for eventual local-multi-instance?
7. **Timeline/budget** appetite given research-grade uncertainty?
8. **OmniGlass sandbox audit** — who owns it, and is it a hard prerequisite for M3?

---

## 15. Anticipated Q&A (be-ready)

**Q: Why NARS over LLM + vector DB + cron?** Emergent pattern learning, an auditable evidence
trail, and explainable adaptive control. The simple stack covers the easy 80%; M0/M2 validate
the rest or kill it.

**Q: How reliable is grounding, really?** The #1 risk and an M0 gate. It is constrained-schema +
embedding-dedup (not "prayer"), but it does **not** filter semantic absurdity and does **not**
survive arbitrary renames — those are why C2 + human confirmation are load-bearing.

**Q: How is it safe if it can act?** The LLM is decoupled from execution (ADR-002): NARS selects
a pre-registered operator + grounded-atom parameters; a typed, allow-listed handler binds them
into an immutable template; OmniGlass sandboxes and (until earned) confirms. No generative ops
exist.

**Q: How do you stop it acting on a coincidence?** ONA grades few-observation contingencies low
(evidential horizon) and *penalizes* mispredictions (assumption-of-failure). We override ONA's
permissive game-agent defaults with a strict autonomy predicate, and human rejection erodes
confounded patterns. NARS handles coincidence by construction; confounding by policy + human loop.

**Q: Permanent vs self-forgetting — contradiction?** No — two tiers: durable pinnable
system-of-record (permanent) + bounded ONA reasoning cache (self-forgetting). Evicted concepts
reload from the store.

**Q: Is this just NARS-GPT?** No — NARS-GPT is the spine for C1 only. C2/C3/C4 and the voice and
execution layers are new work.

**Q: Single riskiest assumption?** That an LLM can *reliably and consistently* translate the
user's world into stable Narsese symbols. If it holds, everything follows; if not, we stop at M0.
