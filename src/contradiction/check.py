"""Contradiction detection core (C2 / M1). Functional Core (S-02) — pure polarity logic.

Scope (verified against ONA): catches DIRECT same-statement polarity contradictions — the LLM
asserts <X --> Y> true while the system already HOLDS <X --> Y> false (or vice versa), at
meaningful confidence. Two things are explicitly out of scope:
- Transitive-derived negations: ONA's deduction zeroes a conclusion's confidence when a premise
  has frequency 0 (conf = c1*c2*f), so derived negations do not propagate. Inherited constraints
  must be materialized as direct statements (future work), not relied on via deduction.
- Competing-value / uniqueness conflicts (e.g. AWS vs GCP): deferred — handled by Truth_Revision.
"""
from __future__ import annotations

from dataclasses import dataclass

from brain import Answer, Truth

DEFAULT_MIN_CONFIDENCE = 0.3


def _asserts_true(truth: Truth) -> bool:
    return truth.frequency >= 0.5


def is_contradiction(
    incoming: Truth, existing: Truth, min_confidence: float = DEFAULT_MIN_CONFIDENCE
) -> bool:
    """True if incoming and existing assert opposite polarity and existing is confident enough."""
    return existing.confidence >= min_confidence and _asserts_true(incoming) != _asserts_true(existing)


@dataclass(frozen=True)
class Conflict:
    """Both sides of a detected contradiction, each with its evidence trail (C2 flag-to-human)."""

    term: str  # the statement asserted with opposite polarity, e.g. "<x --> y>"
    incoming: Truth  # what the LLM proposes
    incoming_statement: str  # the proposed Narsese — the incoming side's evidence (new claim)
    existing: Answer  # ONA's existing belief: truth + stamp = the existing side's evidence trail

    def summary(self) -> str:
        existing = self.existing
        inc = "TRUE" if _asserts_true(self.incoming) else "FALSE"
        ext = "TRUE" if (existing.truth and _asserts_true(existing.truth)) else "FALSE"
        et = existing.truth
        return (
            f"CONTRADICTION on {self.term}: "
            f"incoming asserts {inc} ({self.incoming_statement!r}); "
            f"memory holds {ext} "
            f"(freq={et.frequency if et else None}, conf={et.confidence if et else None}, "
            f"evidence stamp={existing.stamp}, creationTime={existing.creation_time})."
        )
