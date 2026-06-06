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
from .ground import cosine_similarity
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


# ── L1 — the semantic embedding bridge (empirically calibrated; L2 removed) ──
# Bands measured against validation_corpus.json DEFER quadrant (synonym floor 0.946,
# hallucination ceiling 0.745). The ambiguous gap routes to the HUMAN, never to an LLM —
# the user is the safest, most-deterministic arbiter of ambiguity (see corpus _meta).
THRESHOLD_ACCEPT = 0.90
THRESHOLD_REJECT = 0.80


class Decision(Enum):
    COMMIT = "commit"       # write to the L2 system-of-record
    REJECT = "reject"       # hard bounce (show the educational mirror)
    ESCALATE = "escalate"   # ambiguous -> show the round-trip, prompt the human [y/n]


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    layer: str                       # "L0" (structural) or "L1" (semantic)
    reason: str
    cosine: float | None = None      # set for L1
    back_render: str | None = None   # the canonical English mirror (for L1 reject/escalate UX)


def back_render(claim: Claim) -> str:
    """Deterministic, syntactically-sterile English mirror of a claim (locked templates).

    No verb conjugation by design: 'coffee cause alert' is grammatically wrong but semantically
    pure — the embedder handles raw tokens better than invented conjugations.
    """
    if isinstance(claim, RelationClaim):
        if claim.verb.strip().lower() in _TAXONOMIC_VERBS:
            return f"{claim.subject} is not a {claim.object}." if claim.negated \
                else f"{claim.subject} is a {claim.object}."
        return f"{claim.subject} does not {claim.verb} {claim.object}." if claim.negated \
            else f"{claim.subject} {claim.verb} {claim.object}."
    return f"{claim.subject} is not {claim.value}." if claim.negated \
        else f"{claim.subject} is {claim.value}."


def l1_band(cosine: float) -> Decision:
    """Pure threshold classifier for the semantic bridge."""
    if cosine >= THRESHOLD_ACCEPT:
        return Decision.COMMIT
    if cosine < THRESHOLD_REJECT:
        return Decision.REJECT
    return Decision.ESCALATE


class IngestionGate:
    """The full ingestion gate: L0 (structural, pure) -> L1 (semantic embedding).

    L2 (an LLM arbiter) was removed: the empirical cosine gap is wide and clean, and the human is
    the safest arbiter of the rare ambiguous case — so the [0.80, 0.90) band ESCALATES to a [y/n],
    never to a stochastic judge. Zero generative-model calls on the ingestion path.
    """

    def __init__(self, embedder: object) -> None:
        self._embedder = embedder  # duck-typed: .embed(text) -> list[float]

    def _embed(self, text: str) -> list[float]:
        vec = self._embedder.embed(text)  # type: ignore[attr-defined]
        return vec[0] if vec and isinstance(vec[0], list) else vec

    def evaluate(self, claim: Claim, source: str) -> GateResult:
        r0 = validate_l0(claim, source)
        if r0.verdict is L0.ACCEPT:
            return GateResult(Decision.COMMIT, "L0", r0.reason)
        if r0.verdict is L0.REJECT:
            return GateResult(Decision.REJECT, "L0", r0.reason)
        # L0 DEFER -> L1 semantic check (the only place an embedding is computed).
        mirror = back_render(claim)
        cos = cosine_similarity(self._embed(source), self._embed(mirror))
        return GateResult(l1_band(cos), "L1", f"semantic cosine {cos:.3f}",
                          cosine=cos, back_render=mirror)
