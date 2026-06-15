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
