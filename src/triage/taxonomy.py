"""Slice 1: the Clause-Type Taxonomy + deterministic fast-pass classifier (Functional Core, S-02).

The curated, on-device ontology that is the normalization key for salience (now) and cross-document
deviation (Slice 2). The V1 classifier is DETERMINISTIC and LEXICON-based (no model on the critical path),
multi-label, and FAIL-OPEN: a clause matching no lexicon becomes UNCLASSIFIED -> needs_review -> salient,
so it is surfaced for manual reading, NEVER silently dropped. The rigorous, function-grounded 7B classifier
(the semantic "liquidated-damages-is-really-a-cap" case at depth) is Slice 2 — out of scope here. Note the
lexicon keys on FUNCTION phrases, not headings: a heading is a weak hint, the operative language decides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ClauseType(Enum):
    INDEMNIFICATION = "indemnification"
    LIMITATION_OF_LIABILITY = "limitation_of_liability"
    BREACH_NOTIFICATION = "breach_notification"
    CONFIDENTIALITY = "confidentiality"
    TERM_AND_TERMINATION = "term_and_termination"
    AUTO_RENEWAL = "auto_renewal"
    GOVERNING_LAW = "governing_law"
    DISPUTE_RESOLUTION = "dispute_resolution"
    IP_ASSIGNMENT = "ip_assignment"
    DATA_PROTECTION = "data_protection"
    PAYMENT_TERMS = "payment_terms"
    WARRANTIES = "warranties"
    FORCE_MAJEURE = "force_majeure"
    ASSIGNMENT = "assignment"
    OTHER = "other"
    UNCLASSIFIED = "unclassified"


# Salience tuning surface (single place to tune): 3 = bites hardest ... 0 = boilerplate.
# `salient` := weight >= _SALIENT_AT. UNCLASSIFIED is weighted salient on purpose (fail-open).
_SALIENT_AT = 2
RISK_WEIGHT: dict[ClauseType, int] = {
    ClauseType.INDEMNIFICATION: 3, ClauseType.LIMITATION_OF_LIABILITY: 3, ClauseType.BREACH_NOTIFICATION: 3,
    ClauseType.IP_ASSIGNMENT: 3, ClauseType.DATA_PROTECTION: 3, ClauseType.AUTO_RENEWAL: 3,
    ClauseType.TERM_AND_TERMINATION: 2, ClauseType.DISPUTE_RESOLUTION: 2, ClauseType.GOVERNING_LAW: 2,
    ClauseType.CONFIDENTIALITY: 2, ClauseType.ASSIGNMENT: 2,
    ClauseType.WARRANTIES: 1, ClauseType.PAYMENT_TERMS: 1, ClauseType.FORCE_MAJEURE: 1,
    ClauseType.OTHER: 0, ClauseType.UNCLASSIFIED: 2,
}

# Function-phrase lexicon (lowercased). Versioned, auditable artifact — the tuning surface for typing.
LEXICON: dict[ClauseType, tuple[str, ...]] = {
    ClauseType.INDEMNIFICATION: ("indemnif", "hold harmless", "defend and hold"),
    ClauseType.LIMITATION_OF_LIABILITY: ("in no event shall", "aggregate liability", "limitation of liability",
                                         "shall not exceed", "liquidated damages", "consequential damages",
                                         "liability exceed"),
    ClauseType.BREACH_NOTIFICATION: ("breach notification", "notify", "notification", "data breach",
                                     "security incident"),
    ClauseType.CONFIDENTIALITY: ("confidential information", "strict confidence", "non-disclosure",
                                 "keep confidential"),
    ClauseType.TERM_AND_TERMINATION: ("terminate", "termination", "expiration", "term of this agreement"),
    ClauseType.AUTO_RENEWAL: ("automatically renew", "auto-renew", "renew for successive", "evergreen"),
    ClauseType.GOVERNING_LAW: ("governed by", "laws of the state", "governing law", "construed in accordance"),
    ClauseType.DISPUTE_RESOLUTION: ("arbitration", "arbitrator", "exclusive venue", "dispute resolution",
                                    "submit to the jurisdiction"),
    ClauseType.IP_ASSIGNMENT: ("intellectual property", "work product", "assigns all right", "ownership of",
                               "grants a license"),
    ClauseType.DATA_PROTECTION: ("personal data", "data security", "sub-processor", "subprocessor",
                                 "process personal", "data breach"),
    ClauseType.PAYMENT_TERMS: ("invoice", "net thirty", "interest at", "payment of fees", "undisputed amounts"),
    ClauseType.WARRANTIES: ("represents and warrants", "warranty", "free from defects", "warrants that"),
    ClauseType.FORCE_MAJEURE: ("force majeure", "beyond its reasonable control", "act of god"),
    ClauseType.ASSIGNMENT: ("assign this agreement", "change of control", "may not assign"),
}


@dataclass(frozen=True)
class ClauseClassification:
    types: tuple[tuple[ClauseType, float], ...]            # (type, confidence) desc; () => unclassified
    salient: bool
    needs_review: bool                                     # True iff UNCLASSIFIED (fail-open)

    @property
    def top_type(self) -> ClauseType:
        return self.types[0][0] if self.types else ClauseType.UNCLASSIFIED


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def classify(span_text: str, heading: str | None = None) -> ClauseClassification:
    """Deterministic, multi-label, fail-open. Heading is a minor hint; the body's function phrases decide."""
    body, head = _norm(span_text), _norm(heading or "")
    scored: list[tuple[ClauseType, float]] = []
    for ctype, phrases in LEXICON.items():
        n = sum(1 for p in phrases if p in body) + sum(1 for p in phrases if p in head)
        if n:
            scored.append((ctype, round(n / (n + 1), 3)))      # 1 hit -> 0.5, 2 -> 0.667, ...
    if not scored:
        return ClauseClassification((), salient=True, needs_review=True)   # UNCLASSIFIED -> surfaced
    scored.sort(key=lambda t: (-t[1], -RISK_WEIGHT[t[0]], t[0].value))
    salient = max(RISK_WEIGHT[t] for t, _ in scored) >= _SALIENT_AT
    return ClauseClassification(tuple(scored), salient=salient, needs_review=False)
