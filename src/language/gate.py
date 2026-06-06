"""L0 — the deterministic structural ingestion gate (M3 / V1). Functional Core (S-02): pure.

L0 validates the typed Claim AST produced by the GBNF layer, BEFORE `compiler.to_narsese()`
serializes it. It NEVER parses Narsese text — there is no shadow of ONA's grammar here, because
the atoms ARE the Claim's fields. ONA's C parser remains the sole authority over Narsese strings.

Verdicts:
  REJECT  structural violation — non-whitelisted shape / non-taxonomic relation verb (out-of-scope
          causal/action) / fused multi-concept atom. Absolute, and NOT human-overridable.
  ACCEPT  structurally sound AND every content atom traces to a source token via an
          inflectional-only stemmer — the zero-latency fast path.
  DEFER   structurally sound but an atom is untraceable -> hand to L1 (the embedding arbiter).
          (Synonyms and hallucinations both land here; only L1's cosine separates them.)

Verified against validation_corpus.json (see language/test_gate.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .compiler import _ISA
from .schema import Claim, PropertyClaim, RelationClaim

# Single source of truth for taxonomic copulas: the compiler's _ISA set, reused so the gate
# can never drift from what the compiler actually treats as inheritance.
_TAXONOMIC_VERBS = frozenset(_ISA)
_WORD = re.compile(r"[a-z0-9]+")


class L0(Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    DEFER = "defer"


@dataclass(frozen=True)
class L0Result:
    verdict: L0
    reason: str


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def stem(word: str) -> str:
    """Conservative INFLECTIONAL-only stemmer (ruling: no derivational suffixes/prefixes).

    Plurals (-s/-es/-ies) and the common verb inflections (-ing/-ed). Deliberately omits -er/-est/
    -ly: those collide with nouns ('computer', 'family') and would cause FALSE-REJECTS at the trace
    step (the dangerous direction). Biased to UNDER-stem; misses fall through to L1, never reject.
    """
    w = word.lower()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"                                  # parties -> party
    if w.endswith(("sses", "shes", "ches", "xes", "zes")):
        return w[:-2]                                        # boxes -> box, classes -> class
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]                                        # ducks -> duck, machines -> machine
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]                            # guessed -> guess (no undouble: runn stays)
    return w


def _content_atoms(claim: Claim) -> list[str]:
    """The user-meaning atoms; the relation copula is structural, not a traced content atom."""
    if isinstance(claim, RelationClaim):
        return [claim.subject, claim.object]
    return [claim.subject, claim.value]


def is_fused(atom: str) -> bool:
    """True if the atom joins more than one word-token (e.g. 'me alert' / 'me_alert')."""
    return len(_words(atom)) != 1


def validate_l0(claim: Claim, source: str) -> L0Result:
    """Deterministic L0 verdict for one claim against its source sentence. Pure, zero-latency."""
    if not isinstance(claim, (RelationClaim, PropertyClaim)):
        return L0Result(L0.REJECT, "non-whitelisted claim shape")
    if isinstance(claim, RelationClaim) and claim.verb.strip().lower() not in _TAXONOMIC_VERBS:
        return L0Result(L0.REJECT, f"non-taxonomic verb {claim.verb!r} (out-of-scope causal/relational)")
    atoms = _content_atoms(claim)
    for a in atoms:
        if is_fused(a):
            return L0Result(L0.REJECT, f"fused multi-concept atom {a!r}")
    source_stems = {stem(t) for t in _words(source)}
    for a in atoms:
        if stem(_words(a)[0]) not in source_stems:
            return L0Result(L0.DEFER, f"atom {a!r} not traceable to source (defer to L1)")
    return L0Result(L0.ACCEPT, "structurally sound; all atoms trace to source")
