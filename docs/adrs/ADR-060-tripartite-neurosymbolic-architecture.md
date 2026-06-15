# ADR-060: The Tripartite Neurosymbolic Architecture

## Status
Accepted — layer 1 built; layers 2–3 specified, keystone empirically validated (toy scale), largely unbuilt.

## Context
2025–2026 market research (this session) established: AI contract review is crowded and well-funded (dozens
of vendors — one analyst report profiles ~25, CB Insights maps 144 legal-tech companies); the leaders are
**cloud-only** (Harvey hosts on Azure; sovereignty via cloud regions, not on-device); and the on-device /
zero-leak niche is real but only **lightly occupied** (SpotDraft + Qualcomm, Jan 2026 — but Snapdragon-
hardware-locked and in limited rollout). We **cannot out-feature** cloud giants from a 16 GB box; the only
durable moat is **zero-leak local + reasoning the cloud cannot verifiably reproduce.**

ADR-059 established that the deviation engine is deterministic (not NARS) and that **NARS is actively *wrong*
for binary legal compliance** (its defeasible/inductive truth-maintenance is a hazard for rules that need
instant, absolute override). That left NARS's legitimate role undefined. This ADR defines how the engines
coexist without poisoning compliance, and confronts the scaling threat (the ontology bottleneck).

## Decision
A **Tripartite engine**, partitioned by epistemic certainty. The contract text **never leaves the device.**

1. **Deterministic Referee** — the firm's playbook as hard rules + forward-chaining over the extracted
   parameters (ADR-059). Owns **binary compliance**: certain, instant-override, auditable modus ponens.
   Produces the *authoritative* findings.
2. **Vector Layer** — local embeddings + cosine. Owns **similarity & novelty** (cheap, fast).
3. **NARS Sidecar** — ONA over the firm's accumulated beliefs. Owns **corpus-wide induction, anomalous-
   omission detection, and compositional (multi-hop) risk with verifiable provenance.** Produces *advisory*
   hypotheses **only — never compliance verdicts.**

**Boundary / firewall:** the compliance verdict is a pure function of `(deterministic playbook ∧ current
doc)` with **zero input from the NARS store** (CI-asserted, mirroring the `cloud_egress` air-gap). The
Referee routes only its **NULLs** (unmatched clauses, expected-but-absent clauses, low-confidence
extractions) up to the Vector/NARS layers. NARS learns **descriptive** patterns, never **prescriptive**
policy; a user "Accept" is **not** a training signal. ⟨f,c⟩ truth values stay internal (used only to
rank/band); the UI renders deterministic findings as *authoritative* and NARS findings as clearly-marked
*"worth a look."*

**Empirical validation** (20-contract spike, real ONA + nomic embeddings, this session):
- *Novelty:* embeddings flagged the novel "audit_rights" clause (cosine 0.571 < 0.6); NARS has no native
  novelty signal → **Vector wins.**
- *Omission:* embeddings flagged the missing liability_cap via centroid coverage (0.499); NARS **induced**
  an expectation `has_liability_cap` (f=1.0, c=0.85) from 20 examples with **no hand-written rule** → **tie;
  NARS's is a reasoned, confidence-weighted expectation.**
- *Compositional risk (decisive):* NARS **derived** `new_audit_clause → high_risk_clause` (f=1.0, c=0.73)
  via `audit → data_access → high_risk`, returning the **provenance chain** (stamp 3,2,1); embeddings could
  only report 0.505 similarity, **no conclusion** → **NARS does what embeddings structurally cannot.**

Conclusion: the layers are **complementary (hybrid)**; NARS's *unique* contribution — over both embeddings
and deterministic logic — is **induction-of-norms + composition-with-provenance in one local engine.**

## Consequences
- **Easier:** a defensible on-device moat (verifiable reasoning + learned norms the cloud can't reproduce);
  hallucination is contained — the LLM never touches the compliance verdict or the runtime reasoning path.
- **Harder:** three engines to build/maintain; **layers 2–3 are unbuilt** (only the deterministic deviation
  engine exists today); multi-hop **confidence decays** with depth (already 0.73 at 2 hops → deep chains are
  weak, advisory-only); ONA **attention-decay at portfolio scale** is unproven; induction reliability beyond
  toys is unproven.

### The Ontology Bottleneck (the existential scaling risk)
Compositional reasoning needs the relation graph (`audit → data_access → risk`). Hand-authoring thousands of
edges by counsel does not scale. **Hypothesis:** a secure **local** LLM proposes candidate edges *offline*
from the corpus, each tied to its source text; a human **approves** each before it becomes a belief; the
approved ontology is **frozen and versioned**, and **runtime reasoning uses only the frozen graph** (no LLM
in the live path → fast, deterministic, firewall intact). Split the graph into **domain-relation edges**
(LLM-proposable, human-verified) vs **risk-policy edges** (counsel-authored, part of the playbook).
**Load-bearing, unsolved risk:** a hallucinated edge a human rubber-stamps poisons **every** contract — an
ontology error has *global* blast radius, unlike a single-clause error — so the approval gate is mandatory
and is itself the scaling constraint (smaller than authoring, not zero). Validating LLM-proposed-ontology
quality + human-approval throughput is the **next spike** and gates the company-level bet.

## Alternatives Considered
- **NARS for binary compliance** — rejected (ADR-059): defeasibility is a hazard for rules needing absolute,
  instant override.
- **Deterministic / Datalog only** — rejected: cannot induce the firm's implicit norms or flag anomalous
  omissions / novelty (the spike's omission + novelty value would be lost).
- **Embeddings only** — rejected: similarity ≠ logical composition; cannot derive "risk" or show a chain
  (spike: 0.505 similarity, no conclusion).
- **Cloud LLM (Harvey-style)** — rejected: breaks the zero-leak moat for confidential matters that legally
  cannot touch the cloud — the one segment we can defensibly win.

## Related
[ADR-059](./ADR-059-document-triage-deviation-engine.md) (the deterministic deviation engine = layer 1).
