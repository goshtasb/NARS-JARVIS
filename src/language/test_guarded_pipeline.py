"""Unit tests for the consensus loop + the claim->Narsese compiler (v1.24.0 extraction redesign).
No real model: consensus is driven by a scripted fake LLM; the compiler is pure. The compiled output is
cross-checked against the production is_valid_belief so what we hand to tell() is guaranteed well-formed."""
import json

from language.consensus import _consensus_key, extract_consensus
from language.guarded_compile import compile_claim, compile_claims
from memory import is_valid_belief


class _ScriptedLLM:
    """Returns a pre-scripted JSON array per call, cycling through `scripts` (one per temperature pass)."""
    def __init__(self, scripts: list[list[dict]]):
        self._scripts = scripts
        self._i = 0

    def generate_json(self, system, user, grammar, max_tokens=256, temperature=0.0):
        out = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return json.dumps(out)


_REL = {"type": "RelationClaim", "deontic": "shall", "subject": "Vendor", "verb": "notify",
        "object": "Customer", "evidence": "Vendor shall notify Customer"}


# ── consensus ──
def test_consensus_keeps_a_binding_stable_across_all_passes() -> None:
    llm = _ScriptedLLM([[_REL], [_REL], [_REL]])
    kept = extract_consensus(llm, "x", temps=(0.0, 0.4, 0.7))
    assert len(kept) == 1 and kept[0]["subject"] == "Vendor"


def test_consensus_drops_a_fluttering_binding() -> None:
    flip = dict(_REL, object="Regulator")          # pass 2 binds a different object
    llm = _ScriptedLLM([[_REL], [flip], [_REL]])
    assert extract_consensus(llm, "x", temps=(0.0, 0.4, 0.7)) == []   # not identical in all 3 -> dropped


def test_consensus_drops_a_polarity_flip() -> None:
    neg = dict(_REL, deontic="shall_not")          # same bindings, OPPOSITE polarity in one pass
    llm = _ScriptedLLM([[_REL], [neg], [_REL]])
    assert extract_consensus(llm, "x", temps=(0.0, 0.4, 0.7)) == []   # polarity must agree


def test_consensus_is_article_insensitive() -> None:
    art = dict(_REL, subject="the Vendor")         # surface article variation must NOT cause a drop
    llm = _ScriptedLLM([[_REL], [art], [_REL]])
    assert len(extract_consensus(llm, "x", temps=(0.0, 0.4, 0.7))) == 1


def test_consensus_key_polarity_distinguishes_shall_not() -> None:
    assert _consensus_key(_REL)[1] == "pos"
    assert _consensus_key(dict(_REL, deontic="shall_not"))[1] == "neg"


# ── compiler (cross-checked against production is_valid_belief) ──
def test_compile_relation_and_property() -> None:
    rel = compile_claim(_REL)
    assert rel == "<(vendor * customer) --> notify>." and is_valid_belief(rel)
    prop = compile_claim({"type": "PropertyClaim", "deontic": "none", "subject": "data",
                          "value": "confidential", "evidence": "e"})
    assert prop == "<data --> [confidential]>." and is_valid_belief(prop)


def test_compile_temporal_binds_deadline_into_predicate() -> None:
    c = {"type": "TemporalClaim", "deontic": "shall", "subject": "Vendor", "action": "notify",
         "object": "Customer", "within_value": "72", "within_unit": "hours", "evidence": "e"}
    out = compile_claim(c)
    assert out == "<(vendor * customer) --> notify_within_72_hours>." and is_valid_belief(out)


def test_compile_conditional_uses_predictive_implication() -> None:
    c = {"type": "ConditionalClaim", "deontic": "shall", "if": "data breach",
         "then": "notify customer", "evidence": "e"}
    out = compile_claim(c)
    assert out == "<data_breach =/> notify_customer>." and is_valid_belief(out)


def test_compile_prohibition_is_negative_truth() -> None:
    c = {"type": "RelationClaim", "deontic": "shall_not", "subject": "Processor", "verb": "engage",
         "object": "subprocessor", "evidence": "e"}
    out = compile_claim(c)
    assert out.endswith("{0.0 0.9}") and is_valid_belief(out)     # prohibition -> evidence-against


def test_compile_rejects_empty_atom() -> None:
    assert compile_claim({"type": "ConditionalClaim", "deontic": "none", "if": "", "then": "x",
                          "evidence": "e"}) is None


def test_compile_claims_filters_none() -> None:
    good = dict(_REL)
    bad = {"type": "ConditionalClaim", "deontic": "none", "if": "", "then": "", "evidence": "e"}
    assert compile_claims([good, bad]) == ["<(vendor * customer) --> notify>."]
