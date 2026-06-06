"""Grounding / deduplication core. Functional Core (S-02) — pure given embeddings.

The dedup threshold (PRD R1) decides whether a new term becomes a *new atom* or grounds to an
existing one, preventing redundant concepts (e.g. 'car' vs 'automobile'). Embeddings are
injected from `embed.LocalEmbedder`; this module performs no I/O.

Known limit (PRD R1): this catches paraphrase/synonym drift (embedding-close), NOT arbitrary
renames (embedding-distant) — those need an explicit identity mapping.
"""
from __future__ import annotations

import math

DEFAULT_THRESHOLD = 0.95


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def nearest_atom(vec: list[float], existing: dict[str, list[float]]) -> tuple[str | None, float]:
    best: str | None = None
    best_sim = -1.0
    for atom, emb in existing.items():
        sim = cosine_similarity(vec, emb)
        if sim > best_sim:
            best, best_sim = atom, sim
    return best, (best_sim if best is not None else 0.0)


def resolve_atom(
    name: str,
    vec: list[float],
    existing: dict[str, list[float]],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[str, bool]:
    """Return (atom_to_use, created_new). Reuse an existing atom if similar enough, else new."""
    atom, sim = nearest_atom(vec, existing)
    if atom is not None and sim >= threshold:
        return atom, False
    return name, True
