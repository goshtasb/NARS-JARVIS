"""The live hybrid-retrieval pipeline (ADR-056 / Gate 2) — Imperative Shell (S-02).

Runs a natural-language question through the full chain against the LIVE L2 store and a real ONA
derivation, returning either a grounded answer + its STAMP provenance, or an honest abstention the daemon
escalates to Cloud:

  Stage 0 parse -> Stage 1 resolve (lexicon) -> Stage 2 traverse -> Stage 3 chain-aware rank
  -> Stage 4 isolated-ONA derive + evidential STAMP -> enrich each stamp term from L2.

Abstention (-> escalate) on ANY of: no entity anchor; no anchor/target resolves into the graph; the
connecting chain exceeds the budget; ONA finds no answer; the answer has an empty stamp. We never emit a
half-grounded guess — the glass box stays honest or steps aside.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from memory.fact import to_statement
from retrieval.query_parse import extract_mentions
from retrieval.ranking import select_grounded
from retrieval.traversal import BeliefGraph, beliefs_from_facts

_MAX_FACTS = 4000          # Phase-5 TODO: replace whole-store load with a SQL term-scoped candidate query


@dataclass
class RecallResult:
    grounded: bool
    answer: str | None = None
    truth: dict | None = None
    provenance: list[dict] = field(default_factory=list)   # enriched stamp beliefs (the "Why" panel)
    reason: str = ""                                        # why we abstained (diagnostic)

    def to_body(self) -> dict:
        if not self.grounded:
            return {"grounded": False, "escalate": "cloud",
                    "text": "I don't have enough in local memory to answer that — Ask Cloud?",
                    "reason": self.reason}
        return {"grounded": True, "answer": self.answer, "truth": self.truth, "provenance": self.provenance}


def _abstain(reason: str) -> RecallResult:
    return RecallResult(grounded=False, reason=reason)


def recall(query: str, *, store, lexicon, brain_factory, now: float,
           max_hops: int = 4, top_k: int = 12) -> RecallResult:
    qm = extract_mentions(query)
    if not qm.anchors:
        return _abstain("no entity anchor in the query")

    # Stage 1: resolve anchors + targets via the deterministic lexicon (exact -> alias). Unresolved
    # mentions fall back to their own atom (which may itself be a graph term); a truly unknown one is
    # simply absent from the graph and contributes nothing.
    anchors = [lexicon.resolve(a) or a for a in qm.anchors]
    targets = {(lexicon.resolve(m) or m) for m in qm.mentions if m not in qm.anchors}

    facts = store.facts_for_reload(limit=_MAX_FACTS)
    graph = BeliefGraph(beliefs_from_facts(facts))
    top = select_grounded(graph, anchors, targets, now=now, max_hops=max_hops, top_k=top_k)
    if not top:
        return _abstain("no local subgraph connects the query (missing facts or chain too deep)")

    question = _question(anchors, targets, {b.narsese for b in top})
    if question is None:
        return _abstain("could not form a question term from the resolved anchors/targets")

    # Stage 4: isolated ONA derivation -> evidential STAMP
    brain = brain_factory()
    try:
        for b in top:
            brain.add_belief(to_statement(b.narsese, b.frequency, b.confidence))
        answer = brain.ask(question)
        prov_terms = brain.evidence_terms(answer.stamp) if answer is not None else []
    finally:
        brain.close()
    if answer is None or not prov_terms:
        return _abstain("local derivation produced no grounded answer")

    # A conclusion citing ITSELF is not provenance: show the premises that derived it. Keep the answer
    # term only when it's the sole evidence (a direct, told belief — "I know this because you told me").
    chain_terms = [t for t in prov_terms if t != answer.term]
    prov_terms = chain_terms or prov_terms
    provenance = [p for p in (_enrich(store, t) for t in prov_terms) if p is not None]
    truth = {"frequency": answer.truth.frequency, "confidence": answer.truth.confidence} if answer.truth else None
    return RecallResult(grounded=True, answer=answer.term, truth=truth, provenance=provenance)


def _question(anchors, targets, present) -> str | None:
    """Form `<anchor --> target>?` from a resolved anchor + target that both appear in the retrieved
    subgraph. Prefer pairs actually present so ONA has the terms to reason over."""
    present_atoms = set()
    for narsese in present:
        present_atoms.update(_atoms(narsese))
    live_anchors = [a for a in anchors if a in present_atoms] or anchors
    live_targets = [t for t in targets if t in present_atoms]
    if not live_anchors or not live_targets:
        return None
    a, t = live_anchors[0], live_targets[0]
    return f"<{a} --> {t}>?" if a != t else None


def _atoms(narsese: str):
    from retrieval.lexicon_ingest import terms_in_narsese
    return terms_in_narsese(narsese)


def _enrich(store, narsese: str) -> dict | None:
    """A bare STAMP term -> a provenance card for the 'Why' panel (english mirror + truth + learned-at)."""
    fact = store.get(narsese)
    if fact is None:
        return {"narsese": narsese}              # cited but not in L2 (rare) — surface the term, no fiction
    return {"narsese": narsese, "english": fact.english or "",
            "frequency": fact.frequency, "confidence": fact.confidence,
            "learned_at": fact.updated_at}
