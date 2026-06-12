"""Gate 2.2 — the Planted Adversarial Corpus (ADR-056 §6.2).

A tightly controlled Narsese world built to BREAK a naive retriever, with ground-truth labels (which
beliefs are load-bearing vs noise) knowable BY CONSTRUCTION — the thing ordinary RAG evals lack. Three
classes per case:

- LOAD-BEARING : the chain that actually derives the answer (must be in top_k; must be in the STAMP).
- SEMANTIC DECOYS : share an atom with the anchor neighborhood (so they ARE retrieved — recall is loose
  by design) but belong to a different logical branch (must NOT appear in the STAMP).
- DISTANT DISTRACTORS : highly confident + recent but structurally unrelated (must NOT be retrieved at
  all — proving proximity gates them before ranking ever sees their tempting truth/recency).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from retrieval.traversal import Belief

NOW = 1_000_000.0
DAY = 86400.0


def _b(narsese: str, f: float = 1.0, c: float = 0.9, age_days: float = 10.0) -> Belief:
    return Belief(narsese, f, c, NOW - age_days * DAY)


@dataclass(frozen=True)
class Case:
    name: str
    query: str                       # the human query
    question: str                    # the Narsese question ONA answers
    anchors: list[str]               # the Stage-1 resolved anchors (entities)
    beliefs: list[Belief]            # the full planted world (shuffled order is irrelevant)
    load_bearing: set[str]           # narsese that must derive the answer
    decoys: set[str]                 # adjacency-retrievable, wrong branch -> must NOT be in the STAMP
    distractors: set[str] = field(default_factory=set)   # unrelated, high conf/recent -> must NOT retrieve


# ── Case: "Why did my tx drop on SOL?"  ->  <solana --> dropped_tx>? ──
# chain:  <solana --> timeout> , <timeout --> dropped_tx>   (deduction)
_LOAD = ["<solana --> timeout>", "<timeout --> dropped_tx>"]
_DECOYS = [
    "<solana --> has_token>",     # 1-hop via solana, but not on the dropped_tx branch
    "<solana --> staking>",       # 1-hop via solana, different concern
    "<timeout --> log_entry>",    # 2-hop via timeout, different concern
]
_CONTEXT = ["<solana --> blockchain>"]                    # 1-hop, relevant domain (fine if retrieved)
_DISTRACTORS = ["<cursor --> editor>", "<chrome --> browser>"]   # unrelated, HIGH conf + brand new

SOLANA_CASE = Case(
    name="solana_tx_drop",
    query="Why did my tx drop on SOL?",
    question="<solana --> dropped_tx>?",
    anchors=["solana"],
    beliefs=(
        [_b(n) for n in _LOAD]
        + [_b(n) for n in _DECOYS]
        + [_b(n) for n in _CONTEXT]
        + [_b(n, f=1.0, c=0.99, age_days=0.0) for n in _DISTRACTORS]   # the temptation: confident + recent
    ),
    load_bearing=set(_LOAD),
    decoys=set(_DECOYS),
    distractors=set(_DISTRACTORS),
)

CASES = [SOLANA_CASE]
