"""Deterministic unit tests for the verification gate (v1.24.0 extraction redesign). No model needed —
the gate is pure: source text + fixed lexicons in, admit/reject out. Anchored on the two real failure
modes the empirical harness surfaced (the 1500-bps fabrication, the 12-month look-back mis-binding)."""
from language.verify_gate import (
    GateResult, conditional_cued, cue_role, evidence_grounded, polarity_ok, sanity_ok, values_grounded,
    verify,
)

_BREACH = "Vendor shall notify Customer in writing within seventy-two (72) hours of discovering any data breach."
_LIAB = "In no event shall either party's aggregate liability exceed the total fees paid by Customer in the twelve (12) months preceding the claim."
_PAY = "Buyer shall pay all undisputed invoices within thirty (30) days of receipt; overdue amounts shall accrue interest at 1.5% per month."


# ── L1 provenance ──
def test_evidence_must_be_verbatim_substring() -> None:
    assert evidence_grounded("within seventy-two (72) hours", _BREACH)
    assert not evidence_grounded("within ninety-nine (99) hours", _BREACH)   # fabricated citation
    assert not evidence_grounded("", _BREACH)


# ── L2 value grounding (the line-item firewall) ──
def test_value_grounding_passes_faithful_numbers() -> None:
    claim = {"type": "TemporalClaim", "deontic": "shall", "subject": "Vendor", "action": "notify",
             "object": "Customer", "within_value": "72", "within_unit": "hours",
             "evidence": "Vendor shall notify Customer in writing within seventy-two (72) hours"}
    assert values_grounded(claim, claim["evidence"]) == []


def test_value_grounding_catches_1500_basis_points() -> None:
    # the smoking gun: source says 1.5% per month; the claim fabricates "1500 basis points".
    claim = {"type": "RelationClaim", "deontic": "shall", "subject": "overdue amounts",
             "verb": "accrue interest", "object": "1500 basis points per month",
             "evidence": "overdue amounts shall accrue interest at 1.5% per month"}
    missing = values_grounded(claim, claim["evidence"])
    assert "1500" in missing and "basis" in missing       # neither is derivable from "1.5% per month"


def test_value_grounding_morphology_is_allowed() -> None:
    claim = {"type": "RelationClaim", "deontic": "none", "subject": "party", "verb": "liable",
             "object": "aggregate liability", "evidence": "either party's aggregate liability"}
    assert values_grounded(claim, claim["evidence"]) == []   # "liable" ~ "liability" (4-char prefix)


# ── L3 cue-role (the mis-binding firewall) ──
def test_cue_role_separates_deadline_from_window() -> None:
    assert cue_role("notify within seventy-two (72) hours") == "deadline"
    assert cue_role("in the twelve (12) months preceding the claim") == "window"
    assert cue_role("for a period of five (5) years from the date") == "duration"
    assert cue_role("interest at 1.5% per month") == "rate"
    assert cue_role("upon thirty (30) days notice") == "uncued"


def test_conditional_cue_detection() -> None:
    assert conditional_cued("if monthly availability falls below 99.9%")
    assert conditional_cued("provided that the affected party notifies")
    assert not conditional_cued("the vendor stores all data in the eea")


# ── L4 sanity ──
def test_sanity_closed_sets() -> None:
    bad_unit = {"type": "TemporalClaim", "deontic": "shall", "within_value": "72", "within_unit": "fortnights"}
    assert "bad_unit" in sanity_ok(bad_unit)
    bad_val = {"type": "TemporalClaim", "deontic": "shall", "within_value": "soon", "within_unit": "hours"}
    assert "nonnumeric_value" in sanity_ok(bad_val)
    assert sanity_ok({"type": "RelationClaim", "deontic": "frobnicate"}) == ["bad_deontic"]


# ── end-to-end verify() ──
def test_verify_admits_a_faithful_deadline() -> None:
    claim = {"type": "TemporalClaim", "deontic": "shall", "subject": "Vendor", "action": "notify",
             "object": "Customer", "within_value": "72", "within_unit": "hours",
             "evidence": "Vendor shall notify Customer in writing within seventy-two (72) hours"}
    r = verify(claim, _BREACH)
    assert r.admit and not r.degraded and r.kept["type"] == "TemporalClaim"


def test_verify_strips_the_12month_lookback_misbinding() -> None:
    # the model confidently bound a look-back WINDOW as a deadline; the gate must fail the slot closed.
    claim = {"type": "TemporalClaim", "deontic": "shall", "subject": "either party", "action": "be liable",
             "object": "aggregate liability", "within_value": "12", "within_unit": "months",
             "evidence": "either party's aggregate liability exceed the total fees paid by Customer in the twelve (12) months preceding the claim"}
    r = verify(claim, _LIAB)
    assert r.admit and r.degraded                       # not rejected outright — degraded
    assert r.kept["type"] == "RelationClaim"            # deadline slot stripped, relational core kept
    assert "within_value" not in r.kept


def test_verify_rejects_fabricated_evidence() -> None:
    claim = {"type": "RelationClaim", "deontic": "shall", "subject": "Vendor", "verb": "notify",
             "object": "Regulator", "evidence": "Vendor shall notify the Regulator immediately"}
    r = verify(claim, _BREACH)                          # "Regulator" / "immediately" not in source
    assert not r.admit and r.kept is None


# ── L5 negation tripwire (Option A) ──
def test_polarity_tripwire_blocks_dropped_negation() -> None:
    # the real force-majeure leak: evidence has "Neither", claim asserts the positive consequent.
    claim = {"type": "ConditionalClaim", "deontic": "shall", "if": "Neither party",
             "then": "shall be liable for any failure to perform",
             "evidence": "Neither party shall be liable for any failure to perform"}
    assert not polarity_ok(claim, claim["evidence"])
    assert not verify(claim, "Neither party shall be liable for any failure to perform").admit


def test_polarity_tripwire_passes_correctly_negated_claim() -> None:
    claim = {"type": "RelationClaim", "deontic": "shall_not", "subject": "Receiving Party",
             "verb": "disclose", "object": "Confidential Information",
             "evidence": "shall not disclose Confidential Information to any third party"}
    assert polarity_ok(claim, claim["evidence"])                 # shall_not satisfies the cue


def test_polarity_tripwire_passes_when_no_inversion_cue() -> None:
    claim = {"type": "RelationClaim", "deontic": "shall", "subject": "Buyer", "verb": "pay",
             "object": "invoices", "evidence": "Buyer shall pay all undisputed invoices"}
    assert polarity_ok(claim, claim["evidence"])                 # no cue -> unaffected


def test_verify_rejects_fabricated_value_even_with_real_evidence() -> None:
    claim = {"type": "RelationClaim", "deontic": "shall", "subject": "overdue amounts",
             "verb": "accrue interest", "object": "1500 basis points per month",
             "evidence": "overdue amounts shall accrue interest at 1.5% per month"}
    r = verify(claim, _PAY)
    assert not r.admit                                  # L2 blocks the line-item fabrication
