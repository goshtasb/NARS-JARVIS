"""Slice 2 — the corpus aggregator + deviation driver: per-kind partitioning (never blended), self-exclusion,
and end-to-end deviation. Uses the real ParamStore (in-memory SQLite)."""
from triage.aggregator import build_baseline, find_deviations
from triage.parameter import Comparison, ParameterKind, normalize
from triage.paramstore import ParamStore
from triage.structure import Anchor

_A = Anchor(1, (0.0, 0.0, 1.0, 1.0))


def _bn(value, unit, qualitative=False):
    return normalize({"raw_quote": f"{value} {unit}", "role": "notification_deadline", "value": value,
                      "unit": unit, "is_qualitative": qualitative},
                     clause_type="breach_notification", anchor=_A)


def test_baseline_partitions_by_kind_never_blends() -> None:
    s = ParamStore()
    try:
        for i in range(30):
            s.add_parameters(f"cal{i}", [_bn("72", "hours")])
        for i in range(8):
            s.add_parameters(f"biz{i}", [_bn("3", "business_days")])
        for i in range(4):
            s.add_parameters(f"q{i}", [_bn("", "none", qualitative=True)])
        cs = build_baseline(s.rows())[("breach_notification", "notification_deadline")]
        kinds = {c.kind: c for c in cs.cohorts}
        assert kinds[ParameterKind.DURATION_CALENDAR.value].n == 30
        assert kinds[ParameterKind.DURATION_CALENDAR.value].median == 72
        assert kinds[ParameterKind.DURATION_BUSINESS.value].n == 8        # SEPARATE cohort, not blended
        assert cs.qualitative_count == 4
        # there is no single blended mean: calendar and business cohorts are distinct objects
        assert len({c.kind for c in cs.cohorts}) == 2
    finally:
        s.close()


def test_deviation_excludes_self_doc() -> None:
    s = ParamStore()
    try:
        s.add_parameters("doc_self", [_bn("24", "hours")])
        s.add_parameters("doc_other", [_bn("72", "hours")])
        baseline = build_baseline(s.rows(exclude_doc_id="doc_self"))
        coh = baseline[("breach_notification", "notification_deadline")].for_kind(
            ParameterKind.DURATION_CALENDAR.value)
        assert coh.n == 1 and coh.median == 72        # self doc's 24h not in its own baseline
    finally:
        s.close()


def test_find_deviation_end_to_end_tighter() -> None:
    s = ParamStore()
    try:
        for i in range(5):
            s.add_parameters(f"d{i}", [_bn("72", "hours")])
        baseline = build_baseline(s.rows())
        findings = find_deviations([_bn("24", "hours")], baseline)
        assert len(findings) == 1
        f = findings[0]
        assert f.verdict.result is Comparison.TIGHTER          # 24h vs 72h-median cohort
        assert f.cohort.kind == ParameterKind.DURATION_CALENDAR.value and f.cohort.median == 72
    finally:
        s.close()


def test_business_days_vs_calendar_cohort_is_looser() -> None:
    s = ParamStore()
    try:
        for i in range(5):
            s.add_parameters(f"d{i}", [_bn("72", "hours")])
        baseline = build_baseline(s.rows())
        findings = find_deviations([_bn("3", "business_days")], baseline)   # business vs calendar cohort
        assert findings[0].verdict.result is Comparison.LOOSER
        assert findings[0].verdict.detail == "open_upper_ge"
    finally:
        s.close()


def test_new_to_corpus_has_no_verdict() -> None:
    s = ParamStore()
    try:
        baseline = build_baseline(s.rows())                    # empty corpus
        findings = find_deviations([_bn("72", "hours")], baseline)
        assert findings[0].verdict is None and findings[0].cohort is None
    finally:
        s.close()
