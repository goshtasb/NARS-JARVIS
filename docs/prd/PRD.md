# Product Brief: JARVIS — The Zero-Leak Neurosymbolic Reasoning Layer

| Field | Value |
| --- | --- |
| Status | **PROPOSED PIVOT — directional source of truth.** *Not* "build-to-this-as-final": the moat (the NARS open-world layer) is **pending validation** (the ontology spike). |
| Version | 2.0.0-draft (contract-review pivot) |
| Date | 2026-06-15 |
| Supersedes (direction) | [PRD-v1-assistant.md](PRD-v1-assistant.md) — the as-built general-assistant record (kept, not deleted) |
| Related | [ADR-059](../adrs/ADR-059-document-triage-deviation-engine.md), [ADR-060](../adrs/ADR-060-tripartite-neurosymbolic-architecture.md), GitHub #24/#26 |

> **Build status (honest, 2026-06-15).** *Built today:* layer-1 deterministic deviation engine + the Risk
> UI + zero-leak/on-device daemon (lazy-evict). *Target / not yet built:* the playbook-fallback redline
> output, the Vector layer, and the **NARS Sidecar** (its keystone capability is validated only at toy scale
> per ADR-060; its scaling rests on the unsolved **Ontology Bottleneck**). This brief is the North Star, not
> a description of shipped functionality.

## Executive Summary
The enterprise legal-AI market is locked in a feature war dominated by cloud-hosted giants deploying frontier
models for generative redlines and memos. That cloud-first paradigm structurally **underserves** a
mission-critical segment: matters that legally, ethically, or contractually cannot leave the device. (The
niche is not empty — SpotDraft+Qualcomm entered it in Jan 2026 — but it is *lightly occupied* and
hardware-locked to Snapdragon AI-PCs.) JARVIS targets this segment with a 100% local, zero-leak cognitive
assistant. Instead of a hallucination-prone local LLM, JARVIS deploys a **Tripartite Neurosymbolic Engine** —
deterministic rule-checking for absolute compliance + a Non-Axiomatic Reasoning System (NARS) for open-world
risk induction. The result is **verifiable, traceable, multi-hop legal reasoning that cloud LLMs cannot
verifiably reproduce** — auditable, with an explicit provenance trail — establishing a moat in the air-gapped
enterprise space.

## Problem Statement
In-house counsel, defense contractors, and attorneys on highly privileged M&A are locked out of the AI
wave: handling hyper-confidential, data-residency-bound documents, they are forbidden from sending paper to
third-party cloud APIs (OpenAI, Azure, Anthropic). Their options are inadequate — massive manual review,
legacy "Ctrl+F" matchers, or hardware-locked local tools on unproven mobile architectures. The cost is
hundreds of hours of unbillable review, compounded by the operational risk of missing transitive liabilities
buried in dense agreements.

## Solution & Vision
JARVIS is a fully local, Apple-Silicon-native app — an autonomous *internal triage* layer for contract
review. It ingests third-party agreements through a Tripartite Engine. First, a local perception model
extracts parameters. Second, a **Deterministic Referee** checks them against the firm's strict, immutable
playbook, substituting pre-approved fallback language to generate a *deterministic* redline. Finally, the
unmapped clauses — the "NULLs" — route to a **NARS Sidecar** and **Vector Layer** that induce implicit firm
norms and compose multi-hop risk hypotheses. The long-term vision: the standard for secure, air-gapped legal
reasoning — defending a firm's risk perimeter without ever connecting to the internet.

## Value Proposition & Differentiation
Differentiation is rooted in physical and epistemic boundaries. **First, zero data egress:** the contract
never leaves the user's machine. **Second, certainty where it applies and honesty where it doesn't:**
- *Binary compliance* is handled by the deterministic Referee via forward-chaining rules → **instant,
  certain, auditable modus-ponens** derivations (paste-into-the-memo authoritative).
- *Open-world risk* is handled by NARS → **confidence-weighted, compositional** hypotheses with an exact
  provenance trail (e.g., *Clause A + Clause B + Historical Norm C ⟹ possible exposure*), surfaced as
  clearly-marked *"worth a look"* — **never** as a compliance verdict, and never claimed as certainty.

Unlike cloud LLMs that **confabulate** their reasoning chains, every JARVIS conclusion is **cited and
traceable to its source** (a clause line or a dated firm position). The deliverable: a decision-ready,
playbook-substituted redlined document + a ranked exception list where every conclusion is verifiable.

## Target Audience & Personas
**Primary:** In-House Counsel / Managing Partner in high-security, regulated environments (defense, finance,
healthcare, strict M&A) — highly educated, risk-averse, on standard corporate **16 GB Apple-Silicon
MacBooks**. They don't want AI to be creative; they want it exhaustive, obedient to the playbook, and secure.
**Secondary:** the **Legal-Ops Architect** who maps the firm's risk playbook into JARVIS's deterministic config.

## Core User Journeys & Jobs-to-be-Done
The core JTBD is **automated triage of inbound third-party paper.** The user drags a confidential MSA into
JARVIS. It parses locally and extracts parameters. The Referee applies the playbook — auto-clearing standard
clauses, flagging explicit violations, dropping in pre-approved fallback text. Simultaneously the NARS
sidecar analyzes the residual unrecognized text against the firm's historical corpus, surfacing anomalous
omissions and transitive risk hypotheses. The attorney receives a clean internally-redlined document + a
prioritized exception/anomaly dashboard — freeing them for high-judgment strategy over rote checking.

## Scope Definition (In / Out)
**In scope (V1 target):** 16 GB Apple-Silicon minimum; the Tripartite Architecture (Deterministic Referee,
Vector Similarity, NARS Sidecar); playbook-fallback substitution for redlining; single-document parameter
extraction; local inductive anomaly detection. *(Of these, only layer-1 deviation + the UI exist today; the
rest is the build target — see Build status above.)*
**Out of scope:** 8 GB legacy hardware; autonomous external negotiation / auto-replying to counterparties;
generative, LLM-authored redlining; any cloud telemetry or API dependency.

## Success Metrics & KPIs
1. **Engineering efficacy** — deterministic perception accuracy vs the G1 extraction gold set; zero
   degradation on local hardware. 2. **Workflow velocity** — % reduction in time-to-first-redline vs fully
   manual review. 3. **Trust & adoption** — acceptance rate of NARS advisory hypotheses (legally useful
   insight, not academic noise).

## Key Assumptions, Risks, Dependencies
The single largest existential threat is the **Ontology Bottleneck.** Compositional reasoning needs a rich
relation graph; hand-authoring it does not scale. *Assumption:* a secure **local** LLM can propose candidate
Narsese edges offline, which a human rapidly verifies and freezes into a versioned graph. *Risk:* if the
human-in-the-loop approval can't scale — or a **hallucinated edge poisons the global graph** (global blast
radius) — the open-world capability collapses. *Dependency:* the 7B perception model running within 16 GB
unified memory alongside the NARS C-engine without swap-freezes (mitigated by lazy-evict; measured ~4.9 GB,
released cleanly on idle).

## Preliminary Technical Considerations
Prime directive: the **epistemic firewall** between the deterministic and NARS layers — CI must permanently
assert the compliance path imports **zero state** from the inductive NARS store (mirroring `cloud_egress`).
Second: ONA's event-driven **attention decay** at portfolio scale — we must engineer pinning / controlled
replay so critical facts don't decay out of the active context before multi-hop deduction completes.
