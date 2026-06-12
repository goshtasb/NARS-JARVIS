"""Stage 0 of the hybrid retrieval pipeline (ADR-056 / Gate 2) — Functional Core (S-02): pure,
deterministic, model-free.

Decompose a messy natural-language query into **surface mentions** — lookup keys for the L2 lexicon,
**never** Narsese terms. The model's job (a later, optional augmentation pass) is only to handle phrasing
this floor misses; the floor itself is deterministic so it is benchmarkable in isolation (the Stage-0
failure mode we flagged: bad anchors poison the whole deterministic traversal).

Two outputs matter downstream:
- `mentions`  — every content token, normalized through `atom()` so it lands in the SAME namespace as
  stored terms (exact-match in Stage 1 then just works).
- `anchors`   — the subset that look like ENTITIES (tickers `SOL`, proper nouns `Solana`, paths, symbols).
  These are the confident anchors that Stage 1 resolves first and uses to constrain the embedder's search
  to a graph neighborhood. Surfacing them here is what makes the neighborhood-constraint possible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared import atom

# Function/question words carry no retrieval signal — they are never lexicon lookup keys.
_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "my", "your", "our", "their", "its",
    "i", "we", "you", "it", "he", "she", "they", "me", "us", "them",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing", "done", "have", "has", "had",
    "what", "why", "how", "when", "where", "who", "which", "whose",
    "on", "in", "at", "of", "to", "for", "from", "with", "by", "as", "into", "onto", "about",
    "and", "or", "but", "if", "then", "so", "than", "because",
    "again", "just", "now", "still", "ever", "any", "some", "no", "not",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")   # words, tickers, paths, hyphenated/underscored ids
_SPLIT_RE = re.compile(r"[/._\-]+")                      # path/dotted/hyphen compounds -> component mentions


@dataclass(frozen=True)
class QueryMentions:
    mentions: list[str]   # normalized content tokens (lookup keys), order-preserving, de-duped
    anchors: list[str]    # subset of `mentions` that look like entities (resolve-first in Stage 1)
    tokens: list[str]     # the raw tokenization, for tests / debugging


def tokenize(query: str) -> list[str]:
    """Split a query into raw surface tokens (original case preserved for anchor detection). Pure."""
    return _TOKEN_RE.findall(query or "")


def _looks_like_entity(raw: str) -> bool:
    """A surface token that reads as an entity/identifier: a path/dotted symbol (~/x.log, tx.routing), an
    ALL-CAPS ticker (SOL, BTC, API), a Capitalized proper noun (Solana, Chrome), or an alphanumeric id
    (M2, A100, GPT4)."""
    if "/" in raw or "." in raw:
        return True
    letters = [c for c in raw if c.isalpha()]
    if len(letters) >= 2 and raw.isupper():                      # SOL, BTC — but not a lone "I"
        return True
    if raw[:1].isupper() and any(c.islower() for c in raw[1:]):  # Solana, Chrome (not all-caps, not lower)
        return True
    if any(c.isupper() for c in raw) and any(c.isdigit() for c in raw):   # M2, A100, GPT4
        return True
    return False


def extract_mentions(query: str) -> QueryMentions:
    """Stage 0: tokens -> normalized content mentions + the entity anchors among them. Deterministic.
    Compound tokens (paths, dotted symbols, hyphenated ids) split into component mentions, and an entity
    token propagates anchor status to all its components."""
    tokens = tokenize(query)
    mentions: list[str] = []
    anchors: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        entity = _looks_like_entity(raw)
        for sub in _SPLIT_RE.split(raw):
            if not sub or sub.lower() in _STOPWORDS or sub.isdigit():
                continue                                          # drop stopwords + pure-numeric noise ('4')
            key = atom(sub)                                       # same normalization as stored Narsese atoms
            if key == "_" or key in seen:
                continue
            seen.add(key)
            mentions.append(key)
            if entity or _looks_like_entity(sub):
                anchors.append(key)
    return QueryMentions(mentions=mentions, anchors=anchors, tokens=tokens)
