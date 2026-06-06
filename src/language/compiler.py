"""Compile typed claims into Narsese. Functional Core (S-02) — pure, deterministic.

This is the LLM-free heart of the language layer: once the grammar has forced valid claims,
turning them into Narsese is pure code — no probabilism, no hallucination.
"""
from __future__ import annotations

from shared import atom

from .schema import Claim, PropertyClaim, RelationClaim

# "Claimed false" is represented as frequency 0 (integrates with NAL truth revision in ONA),
# rather than a one-off negation copula.
_NEG_TV = " {0.0 0.9}"
_ISA = ("isa", "is_a", "is", "are")


def to_narsese(claim: Claim) -> str:
    if isinstance(claim, RelationClaim):
        subject, obj = atom(claim.subject), atom(claim.object)
        if claim.verb.lower() in _ISA:
            term = f"<{subject} --> {obj}>"
        else:
            term = f"<({subject} * {obj}) --> {atom(claim.verb)}>"
    elif isinstance(claim, PropertyClaim):
        term = f"<{atom(claim.subject)} --> [{atom(claim.value)}]>"
    else:  # pragma: no cover - exhaustive over Claim
        raise TypeError(f"unknown claim: {claim!r}")
    return term + "." + (_NEG_TV if claim.negated else "")


def claims_to_narsese(claims: list[Claim]) -> list[str]:
    return [to_narsese(c) for c in claims]
