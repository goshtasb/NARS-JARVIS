"""Slice 2 deterministic core: the Parameter model, the Normalizer, and the partial-order Comparator.

Model-free (AST-guarded). Canonicalization is WITHIN-kind only; cross-kind is a partial order with an
explicit unrankable element. The two anti-false-precision rules live here:
  * length-ambiguous calendar units (months, years) canonicalize to an INTERVAL, never a point;
  * business days canonicalize to a calendar FLOOR with an OPEN upper (n business_days >= n calendar_days),
    so we never fabricate "3 business days = 72h" and never need a (stale-able) holiday calendar.
Per the locked product call, NON-duration roles (money/%/count) are NOT forced into TIGHTER/LOOSER —
their comparison returns the magnitude as a neutral fact (the deviation Finding renders it).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from triage.structure import Anchor


class ParameterKind(Enum):
    DURATION_CALENDAR = "duration_calendar"
    DURATION_BUSINESS = "duration_business"
    MONEY = "money"
    PERCENT = "percent"
    RELATIVE_FEES = "relative_fees"
    COUNT = "count"
    QUALITATIVE = "qualitative"
    UNKNOWN = "unknown"


_UNIT_KIND = {
    "hours": ParameterKind.DURATION_CALENDAR, "days": ParameterKind.DURATION_CALENDAR,
    "weeks": ParameterKind.DURATION_CALENDAR, "months": ParameterKind.DURATION_CALENDAR,
    "years": ParameterKind.DURATION_CALENDAR, "business_days": ParameterKind.DURATION_BUSINESS,
    "usd": ParameterKind.MONEY, "eur": ParameterKind.MONEY, "gbp": ParameterKind.MONEY,
    "percent": ParameterKind.PERCENT, "months_fees": ParameterKind.RELATIVE_FEES,
    "count": ParameterKind.COUNT, "none": ParameterKind.QUALITATIVE,
}
_EXACT_HOURS = {"hours": 1.0, "days": 24.0, "weeks": 168.0}   # exact calendar -> hours
_DUR = {ParameterKind.DURATION_CALENDAR, ParameterKind.DURATION_BUSINESS}


@dataclass(frozen=True)
class Parameter:
    role: str
    clause_type: str
    raw_quote: str
    kind: ParameterKind
    value: float | None            # None when qualitative/unknown (the firewall nullifies)
    unit: str
    canon_lo: float | None         # canonical base (HOURS for durations) lower bound
    canon_hi: float | None         # ==canon_lo for EXACT; None = OPEN (business days)
    is_qualitative: bool
    anchor: Anchor


class Comparison(Enum):
    TIGHTER = "tighter"
    LOOSER = "looser"
    EQUAL = "equal"
    DIFFERS_IN_KIND_UNRANKABLE = "differs_in_kind_unrankable"
    INCOMPARABLE_QUALITATIVE = "incomparable_qualitative"


@dataclass(frozen=True)
class Verdict:
    result: Comparison
    detail: str = ""               # "" | cross_kind | ambiguous_overlap | cross_currency | open_upper_ge
                                   #    | neutral_magnitude (non-duration: magnitude in the Finding, not ranked)


def normalize(raw: dict, *, clause_type: str, anchor: Anchor) -> Parameter:
    """Deterministic raw-extraction-dict -> Parameter. `kind` is DERIVED from unit here (not model-emitted)."""
    unit = raw.get("unit", "none")
    role = raw.get("role", "other")
    rq = raw.get("raw_quote", "")
    kind = _UNIT_KIND.get(unit, ParameterKind.UNKNOWN)

    if bool(raw.get("is_qualitative")) or unit == "none" or kind is ParameterKind.QUALITATIVE:
        return Parameter(role, clause_type, rq, ParameterKind.QUALITATIVE, None, "none", None, None, True, anchor)
    try:
        val = float(raw.get("value", ""))
    except (TypeError, ValueError):
        return Parameter(role, clause_type, rq, ParameterKind.UNKNOWN, None, unit, None, None, False, anchor)

    if kind is ParameterKind.DURATION_CALENDAR:
        if unit in _EXACT_HOURS:
            lo = hi = val * _EXACT_HOURS[unit]
        elif unit == "months":
            lo, hi = val * 28 * 24, val * 31 * 24           # interval — month length is ambiguous
        else:                                               # years
            lo, hi = val * 365 * 24, val * 366 * 24
    elif kind is ParameterKind.DURATION_BUSINESS:
        lo, hi = val * 24.0, None                           # calendar FLOOR; OPEN upper (no holiday calendar)
    else:                                                   # money / percent / relative_fees / count
        lo = hi = val
    return Parameter(role, clause_type, rq, kind, val, unit, lo, hi, False, anchor)


def _compare_duration(new: Parameter, std: Parameter) -> Verdict:
    nlo, nhi, slo, shi = new.canon_lo, new.canon_hi, std.canon_lo, std.canon_hi
    s_hi = float("inf") if shi is None else shi
    n_hi = float("inf") if nhi is None else nhi
    if nlo > s_hi:
        return Verdict(Comparison.LOOSER)
    if nlo >= s_hi and (nhi is None or nhi > s_hi):          # at-least-as-long, with room above
        return Verdict(Comparison.LOOSER, "open_upper_ge")
    if n_hi < slo:
        return Verdict(Comparison.TIGHTER)
    if nhi is not None and shi is not None and nlo == nhi and slo == shi and nlo == slo:
        return Verdict(Comparison.EQUAL)
    detail = "ambiguous_overlap" if new.kind is std.kind else "cross_kind"
    return Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE, detail)


def compare(new: Parameter, std: Parameter) -> Verdict:
    """Partial-order strictness comparison -> locked enum. Durations are fully ranked (longer = looser);
    qualitative/unknown are unrankable; NON-duration magnitude is reported neutrally (not ranked)."""
    if new.is_qualitative or std.is_qualitative or ParameterKind.QUALITATIVE in (new.kind, std.kind):
        return Verdict(Comparison.INCOMPARABLE_QUALITATIVE, "qualitative")
    if ParameterKind.UNKNOWN in (new.kind, std.kind):
        return Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE, "unknown")
    if new.kind in _DUR and std.kind in _DUR:
        return _compare_duration(new, std)
    if new.kind is not std.kind or (new.kind is ParameterKind.MONEY and new.unit != std.unit):
        return Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE,
                       "cross_currency" if ParameterKind.MONEY in (new.kind, std.kind) else "cross_kind")
    if new.canon_lo == std.canon_lo:
        return Verdict(Comparison.EQUAL)
    # non-duration, same kind, differing magnitude: per the mirror-not-advisor call, do NOT rank strictness;
    # the magnitude is surfaced factually by the deviation Finding.
    return Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE, "neutral_magnitude")
