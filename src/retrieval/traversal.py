"""Stage 2 of the hybrid retrieval pipeline (ADR-056 / Gate 2) — deterministic graph traversal.

NOTE ON THE DATA MODEL: L2 has **no edges table**. Facts are stored as Narsese terms
(`memory/fact.py`); the graph is *implicit* in the term structure. So the "edges" are derived: two
atoms are adjacent iff they co-occur in a belief. We build an inverted index (atom -> beliefs containing
it) over the beliefs and BFS the term-sharing graph. A compound like `(solana * timeout)` needs no
special case — `terms_in_narsese` already decomposes it into its atoms (`solana`, `timeout`), so a belief
carrying the compound is indexed under each, and traversal reaches the parent relation and child atoms
naturally.

The Adjacency Constraint is enforced structurally: a belief enters the candidate set ONLY if it shares an
atom with an anchor (1-hop) or with a 1-hop neighbor atom (2-hop). A belief that merely contains a
*textually similar* token — `solanaceae` is a different atom than `solana` — is never indexed under the
anchor, so word-overlap can never masquerade as graph-adjacency. Pure + deterministic (no embedder).
"""
from __future__ import annotations

from dataclasses import dataclass

from retrieval.lexicon_ingest import terms_in_narsese


@dataclass(frozen=True)
class Belief:
    narsese: str
    frequency: float
    confidence: float
    updated_at: float          # L2 timestamp -> Stage-3 recency


class BeliefGraph:
    """An inverted index over L2 beliefs: atom -> the beliefs containing it."""

    def __init__(self, beliefs):
        self._beliefs: list[Belief] = list(beliefs)
        self._terms: list[list[str]] = [terms_in_narsese(b.narsese) for b in self._beliefs]
        self._index: dict[str, list[int]] = {}
        for i, terms in enumerate(self._terms):
            for t in terms:
                self._index.setdefault(t, []).append(i)

    def traverse(self, anchors, *, max_hops: int = 2) -> list[tuple[Belief, int]]:
        """Radial BFS from the resolved anchors over the term-sharing graph. Returns (belief, hop) for
        every structurally-adjacent belief, `hop` = its MINIMUM distance from any anchor (1 or 2). Beliefs
        reachable only beyond `max_hops`, or sharing no atom with the anchor neighborhood, are excluded."""
        live_anchors = [a for a in anchors if a in self._index]
        if not live_anchors:
            return []                                   # no anchor in the graph -> abstain (no neighborhood)
        term_dist: dict[str, int] = {a: 0 for a in live_anchors}
        belief_hop: dict[int, int] = {}
        frontier = set(live_anchors)                    # atoms at the previous distance
        for hop in range(1, max_hops + 1):
            nxt: set[str] = set()
            for term in frontier:
                for idx in self._index.get(term, []):
                    if idx not in belief_hop:
                        belief_hop[idx] = hop           # first encounter == minimum hop
                        for t in self._terms[idx]:
                            if t not in term_dist:
                                term_dist[t] = hop
                                nxt.add(t)
            frontier = nxt
            if not frontier:
                break
        return [(self._beliefs[i], h) for i, h in sorted(belief_hop.items())]


def beliefs_from_facts(facts) -> list[Belief]:
    """Adapter: L2 `Fact` rows -> traversal `Belief` (the fields Stage 2/3 need)."""
    return [Belief(f.narsese, f.frequency, f.confidence, f.updated_at) for f in facts]
