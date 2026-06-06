"""contradiction — the C2 pre-commit hallucination / contradiction check (PRD C2, M1).

Direct same-statement polarity contradictions only (transitive-derived negations and
competing-value/uniqueness conflicts are out of scope — see check.py). Public interface (ADR-001).
"""
from .check import DEFAULT_MIN_CONFIDENCE, Conflict, is_contradiction
from .guard import ContradictionGuard

__all__ = ["ContradictionGuard", "Conflict", "is_contradiction", "DEFAULT_MIN_CONFIDENCE"]
