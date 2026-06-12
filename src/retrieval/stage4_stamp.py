"""Stage 4 of the hybrid retrieval pipeline (ADR-056 / Gate 2) — STAMP-gated provenance.

The trust moat. The Stage-3 top_k beliefs are rehydrated into an ISOLATED OpenNARS instance, the query is
asked, and the answer carries an **evidential stamp** — the IDs of the premises that mathematically
unified to derive it. We map that stamp back to terms via ONA's own `evidence_terms` (real ids only,
never fabricated) and the "Why" panel cites ONLY those.

This decouples RETRIEVAL recall from PROVENANCE: a belief retrieved into top_k but NOT used by the
derivation never enters the stamp, so it can never be cited. A semantic decoy that shares the anchor's
vocabulary is therefore structurally incapable of producing a false citation — the property that makes
the glass box mathematically honest, not merely plausible.
"""
from __future__ import annotations

from memory.fact import to_statement


def stamp_provenance(beliefs, question: str, *, cycles: int = 300):
    """Load `beliefs` into a fresh isolated ONA, ask `question`, and return (answer, provenance_terms).
    `provenance_terms` is the STAMP — the premises that actually unified. Empty if ONA finds no answer."""
    from brain import Brain
    brain = Brain(cycles_per_step=cycles)
    try:
        for b in beliefs:
            brain.add_belief(to_statement(b.narsese, b.frequency, b.confidence))
        answer = brain.ask(question)
        provenance = brain.evidence_terms(answer.stamp) if answer is not None else []
        return answer, provenance
    finally:
        brain.close()


def false_ground(provenance, decoys, distractors) -> set:
    """The Iron Gate metric: any provenance term that is a decoy or a distractor is a FALSE GROUND.
    Target: the empty set (rate 0.00%)."""
    return set(provenance) & (set(decoys) | set(distractors))
