"""Compile GATE-VERIFIED guarded claims into Narsese (v1.24.0 extraction redesign) — Functional Core (S-02).

Pure + deterministic: a verified claim dict -> one Narsese belief string, or None if it cannot be formed
cleanly. This runs ONLY on claims that already survived consensus + the verification gate, so it never has
to defend against fabrication — its job is a faithful, FLATTENED projection that preserves the binding:

  RelationClaim    <(s * o) --> verb>     (or <s --> o> for an is-a verb)
  PropertyClaim    <s --> [value]>
  TemporalClaim    <(s * o) --> action_within_<value>_<unit>>   (deadline bound INTO the predicate atom,
                                                                 so it can never float free)
  ConditionalClaim <if =/> then>          (NAL predictive implication)
  QuantitativeClaim <(s * amount) --> metric>

Polarity: a shall_not/must_not deontic compiles to a NEGATIVE truth value {0.0 0.9} (NAL revision treats
this as evidence-against), so a prohibition is stored as "this relation is false" rather than asserted.
This is a pragmatic v1 encoding — full NAL-7/8 temporal/deontic operators are deliberately out of scope.
"""
from __future__ import annotations

from shared import atom

_ISA = ("isa", "is_a", "is", "are", "be")
_NEG_TV = " {0.0 0.9}"


def _a(x: str) -> str:
    """atom(), but a degenerate result (atom() returns '_' for empty input) collapses to '' so the caller
    can reject the claim instead of emitting a meaningless '_' term."""
    a = atom(x or "")
    return "" if a.strip("_") == "" else a


def compile_claim(claim: dict) -> str | None:
    """Verified claim -> one well-formed Narsese belief, or None if it can't be cleanly formed."""
    t = claim.get("type")
    neg = claim.get("deontic") in ("shall_not", "must_not")
    try:
        if t == "RelationClaim":
            s, v, o = _a(claim["subject"]), claim.get("verb", ""), _a(claim["object"])
            if not (s and o):
                return None
            term = f"<{s} --> {o}>" if v.lower().strip() in _ISA else f"<({s} * {o}) --> {_a(v)}>"
            if not _a(v) and v.lower().strip() not in _ISA:
                return None
        elif t == "PropertyClaim":
            s, val = _a(claim["subject"]), _a(claim["value"])
            if not (s and val):
                return None
            term = f"<{s} --> [{val}]>"
        elif t == "TemporalClaim":
            s, o = _a(claim["subject"]), _a(claim["object"])
            pred = _a(f"{claim['action']} within {claim['within_value']} {claim['within_unit']}")
            if not (s and o and pred):
                return None
            term = f"<({s} * {o}) --> {pred}>"
        elif t == "ConditionalClaim":
            ant, con = _a(claim["if"]), _a(claim["then"])
            if not (ant and con):
                return None
            term = f"<{ant} =/> {con}>"
        elif t == "QuantitativeClaim":
            s, amt, metric = _a(claim["subject"]), _a(claim["amount"]), _a(claim["metric"])
            if not (s and amt and metric):
                return None
            term = f"<({s} * {amt}) --> {metric}>"
        else:
            return None
    except (KeyError, TypeError):
        return None
    return term + "." + (_NEG_TV if neg else "")


def compile_claims(claims: list[dict]) -> list[str]:
    return [b for b in (compile_claim(c) for c in claims) if b]
