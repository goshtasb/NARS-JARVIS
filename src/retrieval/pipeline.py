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
from retrieval.lexicon_ingest import terms_in_narsese
from retrieval.query_parse import extract_mentions
from retrieval.ranking import select_grounded
from retrieval.traversal import BeliefGraph, beliefs_from_facts


@dataclass
class RecallResult:
    grounded: bool
    answer: str | None = None
    truth: dict | None = None
    provenance: list[dict] = field(default_factory=list)   # enriched stamp beliefs (the "Why" panel)
    reason: str = ""                                        # why we abstained (diagnostic)


def _abstain(reason: str) -> RecallResult:
    return RecallResult(grounded=False, reason=reason)


@dataclass
class PlanResult:
    anchors: list           # resolved canonical anchor atoms ([] = zero-anchor query, excluded from FA-LGR)
    targets: set            # resolved canonical target atoms
    beliefs: list | None = None     # None = couldn't ground (no anchor / no subgraph / no question)
    question: str | None = None

    @property
    def groundable(self) -> bool:
        return self.beliefs is not None


def plan(query: str, *, store, lexicon, now: float, max_hops: int = 4, top_k: int = 12) -> PlanResult:
    """Stages 0-3 (parse -> resolve -> traverse -> rank), SYNCHRONOUS — single-digit ms with FTS. Always
    returns the resolved anchors/targets (for the topic hash); `beliefs`/`question` are set only when the
    vault can ground the query. `beliefs` is plain dicts — the only thing the worker is handed."""
    qm = extract_mentions(query)
    # Stage 1: deterministic lexicon resolution (exact -> alias); unresolved mentions fall back to their
    # own atom (which may itself be a graph term); a truly unknown one is simply absent from the graph.
    anchors = [lexicon.resolve(a) or a for a in qm.anchors]
    targets = {(lexicon.resolve(m) or m) for m in qm.mentions if m not in qm.anchors}
    if not anchors:
        return PlanResult(anchors, targets)                  # no entity anchor -> not groundable
    facts = _fetch_neighborhood(store, anchors + list(targets), max_hops=max_hops)
    top = select_grounded(BeliefGraph(beliefs_from_facts(facts)), anchors, targets,
                          now=now, max_hops=max_hops, top_k=top_k)
    if not top:
        return PlanResult(anchors, targets)                  # no connecting subgraph / chain too deep
    question = _question(anchors, targets, {b.narsese for b in top})
    if question is None:
        return PlanResult(anchors, targets)
    beliefs = [{"narsese": b.narsese, "frequency": b.frequency, "confidence": b.confidence} for b in top]
    return PlanResult(anchors, targets, beliefs=beliefs, question=question)


def enrich_provenance(store, answer_term: str, stamp_terms: list[str]) -> list[dict]:
    """A STAMP (bare premise terms from ONA) -> "Why" panel cards from L2. A conclusion citing ITSELF is
    not provenance: show the premises that derived it; keep the answer term only when it's the sole
    evidence (a direct told belief — "I know this because you told me")."""
    chain = [t for t in stamp_terms if t != answer_term]
    terms = chain or stamp_terms
    return [p for p in (_enrich(store, t) for t in terms) if p is not None]


def recall(query: str, *, store, lexicon, brain_factory, now: float,
           max_hops: int = 4, top_k: int = 12) -> RecallResult:
    """In-process convenience (the direct/test path): plan -> derive in a fresh ONA -> enrich. The DAEMON
    uses `plan()` + an off-loop worker instead, so a pathological derivation can't block the select loop."""
    planned = plan(query, store=store, lexicon=lexicon, now=now, max_hops=max_hops, top_k=top_k)
    if not planned.groundable:
        return _abstain("no local subgraph connects the query")
    beliefs, question = planned.beliefs, planned.question
    brain = brain_factory()
    try:
        for b in beliefs:
            brain.add_belief(to_statement(b["narsese"], b["frequency"], b["confidence"]))
        answer = brain.ask(question)
        prov_terms = brain.evidence_terms(answer.stamp) if answer is not None else []
    finally:
        brain.close()
    if answer is None or not prov_terms:
        return _abstain("local derivation produced no grounded answer")
    truth = {"frequency": answer.truth.frequency, "confidence": answer.truth.confidence} if answer.truth else None
    return RecallResult(grounded=True, answer=answer.term, truth=truth,
                        provenance=enrich_provenance(store, answer.term, prov_terms))


def _fetch_neighborhood(store, seeds, *, max_hops):
    """Pull the term-scoped candidate subgraph from L2 via FTS: BFS-expand from the seed atoms up to
    `max_hops`, collecting every fact reachable by a shared atom. Replaces the whole-store recency scan —
    only the relevant neighborhood loads, at any store size (no 4k cliff, no full-table sort). The precise
    hop-distance / adjacency / ranking is still computed downstream by the BeliefGraph over this set."""
    seen_facts: dict = {}
    seen_atoms = set(seeds)
    frontier = set(seeds)
    for _ in range(max_hops):
        if not frontier:
            break
        new_atoms: set = set()
        for f in store.facts_matching(list(frontier)):
            if f.narsese not in seen_facts:
                seen_facts[f.narsese] = f
                for a in terms_in_narsese(f.narsese):
                    if a not in seen_atoms:
                        seen_atoms.add(a)
                        new_atoms.add(a)
        frontier = new_atoms
    return list(seen_facts.values())


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
