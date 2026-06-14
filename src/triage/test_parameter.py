"""Slice 2 — the normalizer + partial-order comparator: the qualitative firewall and the business-vs-calendar
inequality (incl. the locked '3 business days vs 72h -> LOOSER, not EQUAL' edge case)."""
from triage.parameter import Comparison, ParameterKind, compare, normalize
from triage.structure import Anchor

_A = Anchor(1, (0.0, 0.0, 1.0, 1.0))


def _p(value, unit, *, role="notification_deadline", qualitative=False, raw=None):
    return normalize({"raw_quote": raw or f"{value} {unit}", "role": role, "value": value,
                      "unit": unit, "is_qualitative": qualitative},
                     clause_type="breach_notification", anchor=_A)


# ── canonicalization ──
def test_canonicalize_exact_durations() -> None:
    for value, unit, hours in (("72", "hours", 72), ("3", "days", 72), ("1", "weeks", 168)):
        p = _p(value, unit)
        assert p.kind is ParameterKind.DURATION_CALENDAR and p.canon_lo == p.canon_hi == hours


def test_canonicalize_business_days_open_upper() -> None:
    p = _p("3", "business_days")
    assert p.kind is ParameterKind.DURATION_BUSINESS
    assert p.canon_lo == 72 and p.canon_hi is None        # calendar floor, OPEN upper (no holiday calendar)


def test_canonicalize_months_is_interval_not_point() -> None:
    p = _p("3", "months")
    assert p.canon_lo == 3 * 28 * 24 and p.canon_hi == 3 * 31 * 24 and p.canon_lo != p.canon_hi


# ── the locked edge case ──
def test_three_business_days_vs_72h_is_LOOSER() -> None:
    v = compare(_p("3", "business_days"), _p("72", "hours"))
    assert v.result is Comparison.LOOSER and v.detail == "open_upper_ge"   # NOT EQUAL


def test_exact_duration_ordering() -> None:
    assert compare(_p("24", "hours"), _p("72", "hours")).result is Comparison.TIGHTER
    assert compare(_p("72", "hours"), _p("72", "hours")).result is Comparison.EQUAL
    assert compare(_p("120", "hours"), _p("72", "hours")).result is Comparison.LOOSER


def test_cross_kind_unrankable_when_floor_cannot_settle() -> None:
    v = compare(_p("3", "business_days"), _p("120", "hours"))   # floor 72 < 120 -> cannot settle
    assert v.result is Comparison.DIFFERS_IN_KIND_UNRANKABLE and v.detail == "cross_kind"


def test_same_kind_overlap_unrankable() -> None:
    v = compare(_p("3", "months"), _p("90", "days"))           # [2016,2232]h overlaps 2160h
    assert v.result is Comparison.DIFFERS_IN_KIND_UNRANKABLE and v.detail == "ambiguous_overlap"


# ── the qualitative firewall ──
def test_qualitative_is_nullified_and_incomparable() -> None:
    q = _p("", "none", qualitative=True, raw="promptly")
    assert q.kind is ParameterKind.QUALITATIVE
    assert q.value is None and q.canon_lo is None and q.canon_hi is None    # nullified
    assert compare(q, _p("72", "hours")).result is Comparison.INCOMPARABLE_QUALITATIVE


def test_non_duration_magnitude_not_forced_into_strictness() -> None:
    # money differing -> neutral_magnitude (mirror-not-advisor), NOT TIGHTER/LOOSER
    cap_hi = _p("3000000", "usd", role="liability_cap")
    cap_lo = _p("1000000", "usd", role="liability_cap")
    v = compare(cap_hi, cap_lo)
    assert v.result is Comparison.DIFFERS_IN_KIND_UNRANKABLE and v.detail == "neutral_magnitude"
    assert compare(_p("1000000", "usd", role="liability_cap"),
                   _p("1000000", "usd", role="liability_cap")).result is Comparison.EQUAL


def test_normalize_is_deterministic() -> None:
    raw = {"raw_quote": "3 business days", "role": "cure_period", "value": "3",
           "unit": "business_days", "is_qualitative": False}
    assert normalize(raw, clause_type="x", anchor=_A) == normalize(raw, clause_type="x", anchor=_A)
