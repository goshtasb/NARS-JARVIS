"""Slice 2 — the guarded parameter extractor: schema decomposition, consensus, and verify_gate grounding.
Driven by a scripted fake LLM (no real model)."""
import json

from triage.extract import extract_parameters
from triage.parameter import ParameterKind
from triage.structure import Anchor

_A = Anchor(4, (10.0, 20.0, 200.0, 32.0))
_CLAUSE = ("4. Breach Notification\nVendor shall notify Customer within seventy-two (72) hours of any "
           "data breach affecting personal data.")


class _ScriptedLLM:
    def __init__(self, payload):
        self._payload = payload
    def generate_json(self, system, user, grammar, max_tokens=256, temperature=0.0):
        return json.dumps(self._payload)            # identical each pass -> stable across consensus


def test_extracts_and_derives_kind_from_unit() -> None:
    llm = _ScriptedLLM([{"raw_quote": "within seventy-two (72) hours", "role": "notification_deadline",
                         "value": "72", "unit": "hours", "is_qualitative": False}])
    params = extract_parameters(llm, _CLAUSE, clause_type="breach_notification", anchor=_A)
    assert len(params) == 1
    p = params[0]
    assert p.kind is ParameterKind.DURATION_CALENDAR and p.canon_lo == 72 and p.role == "notification_deadline"


def test_grounding_drops_fabricated_raw_quote() -> None:
    # raw_quote is NOT a verbatim subsequence of the clause -> dropped by verify_gate L1
    llm = _ScriptedLLM([{"raw_quote": "within twenty-four (24) hours", "role": "notification_deadline",
                         "value": "24", "unit": "hours", "is_qualitative": False}])
    assert extract_parameters(llm, _CLAUSE, clause_type="breach_notification", anchor=_A) == []


def test_grounding_drops_fabricated_value() -> None:
    # raw_quote is real, but value '99' does not appear in it -> dropped by verify_gate L2
    llm = _ScriptedLLM([{"raw_quote": "within seventy-two (72) hours", "role": "notification_deadline",
                         "value": "99", "unit": "hours", "is_qualitative": False}])
    assert extract_parameters(llm, _CLAUSE, clause_type="breach_notification", anchor=_A) == []


def test_qualitative_param_is_normalized_to_nullified() -> None:
    clause = "Vendor shall notify Customer promptly of any incident."
    llm = _ScriptedLLM([{"raw_quote": "promptly", "role": "notification_deadline",
                         "value": "", "unit": "none", "is_qualitative": True}])
    params = extract_parameters(llm, clause, clause_type="breach_notification", anchor=_A)
    assert len(params) == 1 and params[0].kind is ParameterKind.QUALITATIVE
    assert params[0].value is None and params[0].canon_lo is None


def test_fluttering_param_dropped_by_consensus() -> None:
    class _Flaky:
        def __init__(self): self.calls = 0
        def generate_json(self, system, user, grammar, max_tokens=256, temperature=0.0):
            self.calls += 1
            unit = "hours" if self.calls != 2 else "days"     # pass 2 flutters the unit -> not stable
            return json.dumps([{"raw_quote": "within seventy-two (72) hours", "role": "notification_deadline",
                                "value": "72", "unit": unit, "is_qualitative": False}])
    assert extract_parameters(_Flaky(), _CLAUSE, clause_type="breach_notification", anchor=_A) == []
