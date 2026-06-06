"""Claim schema — the structured target the GBNF grammar enforces. Functional Core (S-02).

The LLM never emits raw Narsese; it emits these typed claims (grammar-constrained JSON),
which the compiler then turns into Narsese. Parsing here is pure (stdlib json only).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

CLAIM_TYPES = (
    "RelationClaim",
    "PropertyClaim",
    "NegatedRelationClaim",
    "NegatedPropertyClaim",
)


@dataclass(frozen=True)
class RelationClaim:
    subject: str
    verb: str
    object: str
    negated: bool = False


@dataclass(frozen=True)
class PropertyClaim:
    subject: str
    value: str
    negated: bool = False


Claim = RelationClaim | PropertyClaim


def _claim_from_dict(d: dict) -> Claim:
    t = d.get("type")
    if t in ("RelationClaim", "NegatedRelationClaim"):
        return RelationClaim(
            str(d["subject"]), str(d["verb"]), str(d["object"]),
            negated=t.startswith("Negated"),
        )
    if t in ("PropertyClaim", "NegatedPropertyClaim"):
        return PropertyClaim(
            str(d["subject"]), str(d["value"]),
            negated=t.startswith("Negated"),
        )
    raise ValueError(f"unknown claim type: {t!r}")


def parse_claims(text: str) -> list[Claim]:
    """Parse the LLM's GBNF-constrained JSON array into typed claims. Pure."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of claims")
    return [_claim_from_dict(d) for d in data]
