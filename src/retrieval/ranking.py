"""Stage 3 of the hybrid retrieval pipeline (ADR-056 / Gate 2) — truth ranking + the hard AIKR budget.

Rank the Stage-2 candidate subgraph by the tri-variate NARS signal and slice to a fixed top-k, so the
rehydrated L1 set and the 7B's context window are bounded BY CONSTRUCTION regardless of how large L2 grows:

    score = (frequency * confidence) * structural_proximity(hop) * temporal_recency(updated_at)

- frequency * confidence : NARS truth — the belief's evidential strength.
- structural_proximity   : decay by hop distance from the anchor (1-hop = 1.0, 2-hop = 0.5).
- temporal_recency       : a SLIGHT decay (range [1 - RECENCY_DECAY, 1.0]) — a tie-breaker between equally
                           strong, equally close facts, never strong enough to outweigh confidence.

Pure + deterministic. `now` is injected (no wall-clock read here).
"""
from __future__ import annotations

from retrieval.traversal import Belief, BeliefGraph

TOP_K = 12                          # the hard budget (AIKR / context-window guarantee)
PROXIMITY = {1: 1.0, 2: 0.5}        # hop -> structural-proximity multiplier
RECENCY_DECAY = 0.1                 # max recency penalty (so recency stays a slight tie-breaker)
RECENCY_WINDOW_S = 90 * 86400.0     # age at which the recency penalty saturates (~90 days)


def _proximity(hop: int) -> float:
    return PROXIMITY.get(hop, 1.0 / (2 ** max(0, hop - 1)))


def _recency(updated_at: float, now: float) -> float:
    age = max(0.0, now - updated_at)
    return 1.0 - RECENCY_DECAY * min(1.0, age / RECENCY_WINDOW_S)   # in [0.9, 1.0]


def score(belief: Belief, hop: int, *, now: float) -> float:
    return (belief.frequency * belief.confidence) * _proximity(hop) * _recency(belief.updated_at, now)


def rank(candidates, *, now: float, top_k: int = TOP_K) -> list[Belief]:
    """Sort (belief, hop) candidates by score (desc) and slice to the hard budget. Deterministic ties:
    higher score, then more-recent, then narsese (so the output is stable for the benchmark)."""
    ordered = sorted(
        candidates,
        key=lambda c: (score(c[0], c[1], now=now), c[0].updated_at, c[0].narsese),
        reverse=True,
    )
    return [b for b, _hop in ordered[:top_k]]


def select(graph: BeliefGraph, anchors, *, now: float, max_hops: int = 2, top_k: int = TOP_K) -> list[Belief]:
    """Stage 2 + Stage 3 end to end: traverse from the anchors, rank, and return the top-k subgraph.
    Empty when no anchor exists in the graph (honest abstention -> no grounding).

    NOTE: this is the chain-BLIND selector — pure score-and-slice. It can evict a distant load-bearing
    premise when high-score 1-hop decoys saturate the budget. Use `select_grounded` when the query needs a
    deep derivation chain (Gate 2.3 Saturated-Budget)."""
    return rank(graph.traverse(anchors, max_hops=max_hops), now=now, top_k=top_k)


def select_grounded(graph: BeliefGraph, anchors, targets, *, now: float, max_hops: int = 4,
                    top_k: int = TOP_K) -> list[Belief]:
    """Chain-AWARE Stage 3 (the Saturated-Budget fallback): PIN the connecting chain from anchors to
    targets, then fill the remaining budget by score. A flood of high-score 1-hop decoys competes only for
    the leftover slots and can never evict the load-bearing path. If the chain alone exceeds top_k, abstain
    (AIKR: the proof is too deep for the local context -> escalate, never emit a truncated half-chain)."""
    chain = graph.connecting_paths(anchors, targets, max_hops=max_hops)
    chain_terms = {b.narsese for b in chain}
    if len(chain_terms) > top_k:
        return []                                          # honest abstention: chain doesn't fit
    candidates = graph.traverse(anchors, max_hops=max_hops)
    rest = [(b, h) for (b, h) in candidates if b.narsese not in chain_terms]
    filler = rank(rest, now=now, top_k=top_k - len(chain_terms))
    return list(chain) + filler                            # load-bearing chain first, then best context
