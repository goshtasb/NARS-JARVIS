# spikes

Throwaway, reproducible experiments that validate (or kill) an architectural assumption **before** it's
built. Not production code; not part of the test suite. Each script is self-contained and prints its result.

## `tripartite_openworld_spike.py` — NARS vs embeddings, open-world tasks (ADR-060)
The bake-off behind [ADR-060](../docs/adrs/ADR-060-tripartite-neurosymbolic-architecture.md). On a dummy
20-contract corpus it pits a local embedding baseline (nomic via `llama_cpp`) against the real ONA engine
(`brain.Brain`) on three open-world tasks: **novelty**, **anomalous omission**, and **compositional risk**.

Run (from repo root, needs the ONA binary at `OpenNARS-for-Applications/NAR` and the nomic GGUF in `models/`):
```sh
python3 spikes/tripartite_openworld_spike.py
```

**Result captured in ADR-060 (this run):**
- Novelty → embeddings win (cosine 0.571 flagged; NARS has no native novelty signal).
- Omission → tie (embeddings centroid 0.499; NARS induced `has_liability_cap` f=1.0, c=0.85, no hand-written rule).
- Compositional risk → **NARS wins**: derived `audit → data_access → high_risk` (f=1.0, c=0.73) **with a
  provenance chain**; embeddings could only report 0.505 similarity, no conclusion.

**Honest scope:** toy scale (3 hand-fed ontology edges, 20 trivial contracts). Proves the *capability exists*
on the real engine; does **not** prove it scales, that the ontology is authorable, or that multi-hop
confidence holds at depth. See ADR-060 "The Ontology Bottleneck."

## `ontology_edge_proposal.py` — can a 7B *propose* the ontology edges? (ADR-060 bottleneck)
The previous spike **hand-fed** the ontology edges; this one tests the harder, real question from ADR-060's
"Ontology Bottleneck": can a local 7B *generate* `(source, relation, target)` edges from raw clause text,
under a GBNF grammar + closed risk-concept vocab + 3× temperature-consensus + verbatim-quote grounding?

Run (needs the 7B GGUF in `models/`; CUAD was unavailable offline, so 16 curated real-style clauses stand in):
```sh
python3 spikes/ontology_edge_proposal.py
```

**Result (first run): NEGATIVE / inconclusive — reported as-is, not spun.**
- **Yield collapse:** only **2 edges** survived from 16 clauses; **density 0.12 edges/clause** — the failure
  is severe *under*-extraction, the opposite of the feared combinatorial explosion.
- **Grounding ≠ correctness (empirically):** both survivors passed the verbatim-quote gate (0% fabricated
  quotes), yet one — `indemnity --requires--> indemnity` — is a **logically degenerate self-loop**. A
  nonsense edge cleared the grounding gate.
- **Dedup/fatigue hypothesis: untestable** at this yield (nothing to deduplicate).
- **κ NOT COMPUTED** — no independent second labeler available in this sandbox (no human; no API key for a
  frontier proxy; offline). Not faked.

**Prime suspect:** strict **3-of-3 full-tuple consensus** over-filtered — a generative 7B mapping free text
onto a closed vocab doesn't pick the identical canonical tuple three times running, so `stable_across`
annihilated almost everything. Partly a harness artifact, not purely model weakness. **Needs a controlled
re-run** (relax to 2-of-3, add few-shot exemplars, forbid self-loops in the grammar) to separate
model-weakness from filter-strictness before any conclusion.

**Bottom line:** the ontology bottleneck is **harder than the hand-fed toy implied**. This first run does not
validate the "LLM proposes edges" leg of ADR-060 — it's a red flag requiring iteration.
