"""Slice 3a — the deviation-scan orchestrator + the pure event-contract serializer.

Two layers: (1) the pure guardrail (render_class / is_surfaced / build_scan_body) — proves the Mirror-not-
Advisor rendering is decided server-side and EQUAL / new-to-corpus never surface; (2) end-to-end scan_document
driven by a fake StructureSensor + a scripted LLM + the real in-memory ParamStore — proves only SALIENT
spans are extracted, the new doc is excluded from its own baseline, and a real TIGHTER deviation surfaces.
"""
import json

from triage.aggregator import Cohort, Finding
from triage.devscan import build_scan_body, is_surfaced, render_class, scan_document
from triage.parameter import Comparison, Verdict, normalize
from triage.paramstore import ParamStore
from triage.structure import Anchor, DocumentStructure, Span

_A = Anchor(1, (0.0, 0.0, 1.0, 1.0))


def _param(value, unit, *, role="notification_deadline", clause_type="breach_notification", qualitative=False):
    return normalize({"raw_quote": f"{value} {unit}", "role": role, "value": value, "unit": unit,
                      "is_qualitative": qualitative}, clause_type=clause_type, anchor=_A)


# ── the pure rendering guardrail (decided once, server-side) ──
def test_render_class_maps_each_verdict() -> None:
    assert render_class(Verdict(Comparison.TIGHTER)) == "strict"
    assert render_class(Verdict(Comparison.LOOSER, "open_upper_ge")) == "strict"
    assert render_class(Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE, "neutral_magnitude")) == "neutral"
    assert render_class(Verdict(Comparison.DIFFERS_IN_KIND_UNRANKABLE, "cross_kind")) == "unrankable"
    assert render_class(Verdict(Comparison.INCOMPARABLE_QUALITATIVE, "qualitative")) == "qualitative"
    assert render_class(Verdict(Comparison.EQUAL)) == "equal"
    assert render_class(None) == "informational"                       # new to corpus


def test_is_surfaced_drops_equal_and_new_to_corpus() -> None:
    assert is_surfaced(Verdict(Comparison.TIGHTER)) is True
    assert is_surfaced(Verdict(Comparison.INCOMPARABLE_QUALITATIVE, "qualitative")) is True
    assert is_surfaced(Verdict(Comparison.EQUAL)) is False             # not a deviation
    assert is_surfaced(None) is False                                  # new to corpus is not a deviation


def test_build_scan_body_filters_equal_and_carries_baseline() -> None:
    coh = Cohort("duration_calendar", 5, 72.0, 72.0, 72.0)
    findings = [Finding(_param("24", "hours"), Verdict(Comparison.TIGHTER), coh),
                Finding(_param("72", "hours"), Verdict(Comparison.EQUAL), coh)]
    body = build_scan_body("nda.pdf", "deadbeef", 2, findings)
    assert body["state"] == "populated" and len(body["findings"]) == 1   # EQUAL dropped
    f = body["findings"][0]
    assert f["render"] == "strict" and f["verdict"] == "TIGHTER" and f["this"]["value"] == 24.0
    assert f["baseline"] == {"kind": "duration_calendar", "median": 72.0, "n": 5}


def test_build_scan_body_empty_when_no_surfaced_findings() -> None:
    body = build_scan_body("nda.pdf", "id", 3, [Finding(_param("72", "hours"), None, None)])  # new to corpus
    assert body["state"] == "empty" and body["findings"] == []


# ── end-to-end scan: fake sensor + scripted LLM + real ParamStore ──
class _FakeSensor:
    def __init__(self, spans):
        self._spans = tuple(spans)
    def parse(self, path):
        return DocumentStructure(self._spans, 1, False, "")


class _ScriptedLLM:
    """Returns a fixed param payload each pass (stable across consensus); records the clauses it was asked
    about, so the test can PROVE non-salient spans were never sent to the model."""
    def __init__(self, payload):
        self._payload = payload
        self.seen: list[str] = []
    def generate_json(self, system, user, grammar, max_tokens=256, temperature=0.0):
        self.seen.append(user)
        return json.dumps(self._payload)


_SALIENT = Span(text="Vendor shall notify Customer within twenty-four (24) hours.",
                heading="Breach Notification", number="4", anchor=Anchor(1, (0.0, 0.0, 10.0, 10.0)))
_BOILER = Span(text="Vendor warrants that the software is free from defects.",
               heading="Warranty", number="9", anchor=Anchor(1, (0.0, 20.0, 10.0, 30.0)))
_PAYLOAD = [{"raw_quote": "within twenty-four (24) hours", "role": "notification_deadline",
             "value": "24", "unit": "hours", "is_qualitative": False}]


def _seed_baseline(store, value="72", unit="hours", n=5):
    for i in range(n):
        store.add_parameters(f"seed{i}", [_param(value, unit)])


def test_scan_surfaces_tighter_deviation_and_extracts_only_salient_spans() -> None:
    store = ParamStore()
    try:
        _seed_baseline(store)                       # corpus standard: 72h breach-notification deadline
        pendings: list[int] = []
        llm = _ScriptedLLM(_PAYLOAD)
        body = scan_document("nda.pdf", llm=llm, store=store, sensor=_FakeSensor([_SALIENT, _BOILER]),
                             doc_id="newdoc", on_pending=pendings.append)
        # only the salient (breach) span reached the model — the weight-1 warranty span was skipped
        assert pendings == [1]
        assert all("warrants" not in clause for clause in llm.seen)
        assert any("notify Customer" in clause for clause in llm.seen)
        # the deviation surfaces against the user's OWN 72h cohort
        assert body["state"] == "populated" and len(body["findings"]) == 1
        f = body["findings"][0]
        assert f["clause_type"] == "breach_notification" and f["verdict"] == "TIGHTER"
        assert f["render"] == "strict" and f["baseline"]["median"] == 72 and f["baseline"]["n"] == 5
        assert f["this"]["value"] == 24.0 and f["this"]["unit"] == "hours"
    finally:
        store.close()


def test_scan_excludes_doc_from_its_own_baseline_empty_when_new_to_corpus() -> None:
    store = ParamStore()
    try:
        llm = _ScriptedLLM(_PAYLOAD)                 # empty corpus: nothing to deviate against
        body = scan_document("nda.pdf", llm=llm, store=store, sensor=_FakeSensor([_SALIENT]),
                             doc_id="newdoc")
        assert body["state"] == "empty" and body["findings"] == []
        # but the param WAS persisted (it becomes part of the baseline for the NEXT document)
        assert len(store.rows()) == 1 and store.rows()[0]["doc_id"] == "newdoc"
    finally:
        store.close()
