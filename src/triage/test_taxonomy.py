"""Slice 1 / AC2 — fast-pass classification: function-based, multi-label, over-inclusive, fail-open.
Includes the two crucible fixtures Synapse named (Liquidated-Damages-as-cap; data-security-under-Misc)."""
from triage.taxonomy import ClauseType, classify


def _types(c):
    return {t for t, _ in c.types}


# ── AC2 ──
def test_liquidated_damages_classified_as_liability_cap() -> None:
    # heading says "Liquidated Damages"; FUNCTION is a liability cap. Must surface as LoL + salient.
    c = classify("In no event shall either party's aggregate liability exceed the fees paid. "
                 "The parties agree such liquidated damages are reasonable.", heading="Liquidated Damages")
    assert ClauseType.LIMITATION_OF_LIABILITY in _types(c)
    assert c.salient and not c.needs_review


def test_data_security_under_miscellaneous_still_surfaces() -> None:
    # heading is non-descriptive; body is data protection. Heading must NOT be the decision.
    c = classify("Provider shall process all personal data and maintain data security; any sub-processor "
                 "must be approved.", heading="Miscellaneous")
    assert ClauseType.DATA_PROTECTION in _types(c) and c.salient


def test_unrecognized_clause_is_unclassified_review_and_salient() -> None:
    c = classify("This Agreement may be executed in counterparts, each deemed an original.",
                 heading="Counterparts")
    assert c.types == ()                       # nothing matched
    assert c.needs_review and c.salient        # FAIL-OPEN: surfaced for manual review, not hidden
    assert c.top_type is ClauseType.UNCLASSIFIED


def test_multi_label_clause_gets_all_matching_types() -> None:
    c = classify("Vendor shall notify Customer within seventy-two hours of any data breach affecting "
                 "personal data.", heading="Breach Notification")
    ts = _types(c)
    assert ClauseType.BREACH_NOTIFICATION in ts and ClauseType.DATA_PROTECTION in ts


def test_heading_is_only_a_hint_not_the_decision() -> None:
    # a clause MISLABELED "Confidentiality" but functioning as indemnification -> indemnification wins inclusion
    c = classify("Supplier shall indemnify and hold harmless the Buyer.", heading="Confidentiality")
    assert ClauseType.INDEMNIFICATION in _types(c)


def test_classification_is_deterministic() -> None:
    args = ("Supplier shall indemnify and hold harmless the Buyer.", "Indemnification")
    assert classify(*args) == classify(*args)
