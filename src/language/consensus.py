"""Perturbation-consensus for guarded extraction (v1.24.0 extraction redesign) — Functional Core-ish (S-02).

Option B, the catch-all for the residual the deterministic gate cannot see: lexically-clean SEMANTIC
INVERSIONS. We run the guarded extraction N times under temperature jitter (off-loop, overnight, AC-gated
— inference cost is irrelevant; data integrity is not). A binding the model is CONFIDENT in is STABLE
across passes; a binding it is hallucinating from dense legalese FLUTTERS. The consensus rule is strict:
a claim survives only if its (type, polarity, relational bindings) key appears in EVERY pass.

`_consensus_key` deliberately EXCLUDES the evidence span (which varies) and is article/stopword-insensitive,
but is exact on the bindings and polarity — so 'shall' vs 'must' agree (same positive polarity) while
'shall' vs 'shall_not' do NOT (a polarity flip is exactly what we want to catch).
"""
from __future__ import annotations

from .guarded_extract import extract_guarded
from .verify_gate import _STOP, _norm

# Perturbation magnitude (measured on the legal corpus): 0.0/0.2/0.35 holds precision at 1.0 (zero leaks)
# with recall 0.529, vs 0.467 at the aggressive 0.0/0.4/0.7 — same zero leaks, higher recall. Pass 0 is
# always temp 0 (the canonical form we emit); passes 1-2 are the stability probe.
_DEFAULT_TEMPS = (0.0, 0.2, 0.35)
_KEY_FIELDS = {
    "RelationClaim": ("subject", "verb", "object"),
    "PropertyClaim": ("subject", "value"),
    "TemporalClaim": ("subject", "action", "object", "within_value", "within_unit"),
    "ConditionalClaim": ("if", "then"),
    "QuantitativeClaim": ("subject", "metric", "amount"),
}


def _key_norm(s: str) -> str:
    """Normalize a field for matching: lowercase alnum, drop articles/stopwords (so 'the Vendor' == 'Vendor')."""
    return " ".join(t for t in _norm(str(s).replace("_", " ")).split() if t not in _STOP)


def _consensus_key(claim: dict) -> tuple:
    t = claim.get("type", "?")
    polarity = "neg" if claim.get("deontic") in ("shall_not", "must_not") else "pos"
    core = tuple(_key_norm(claim.get(f, "")) for f in _KEY_FIELDS.get(t, ()))
    return (t, polarity) + core


def stable_across(passes: list[list[dict]], key_fn) -> list[dict]:
    """Generic perturbation-consensus filter (reused by claims AND parameters): keep the FIRST pass's items
    whose `key_fn` appears in EVERY pass, de-duped by key. An item that flutters across passes is dropped."""
    if not passes or not passes[0]:
        return []
    stable = set.intersection(*[{key_fn(x) for x in p} for p in passes])
    seen: set = set()
    out = []
    for x in passes[0]:                          # emit the canonical (temp-0) form, de-duped
        k = key_fn(x)
        if k in stable and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def extract_consensus(llm, text: str, temps: tuple = _DEFAULT_TEMPS) -> list[dict]:
    """Run guarded extraction once per temperature; return the temp-0 (canonical) claims whose binding key
    appeared in EVERY pass. A claim whose bindings or polarity flutter across passes is dropped."""
    return stable_across([extract_guarded(llm, text, temperature=t) for t in temps], _consensus_key)
