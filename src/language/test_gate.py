"""Ingestion gate (L0 structural + L1 semantic) — verified against validation_corpus.json."""
import json
import math
import os
from pathlib import Path

from language.gate import (
    L0,
    Decision,
    IngestionGate,
    back_render,
    is_fused,
    l1_band,
    stem,
    validate_l0,
)
from language.schema import parse_claims

_CORPUS = Path(__file__).resolve().parent / "validation_corpus.json"


def _claim(d: dict):
    return parse_claims(json.dumps([d]))[0]


def _l0_verdict(case: dict):
    return validate_l0(_claim(case["claim"]), case["english"])


class _FakeEmbedder:
    """Deterministic stand-in: returns the vector mapped to each exact text. No model."""
    def __init__(self, mapping: dict): self._m = mapping
    def embed(self, text: str): return self._m[text]


def test_stem_is_inflectional_only() -> None:
    assert stem("ducks") == "duck" and stem("birds") == "bird"
    assert stem("mammals") == "mammal" and stem("machines") == "machine"
    assert stem("boxes") == "box" and stem("parties") == "party"
    # A word and its plural MUST share a stem (the -er over-stem trap on 'computer').
    assert stem("computer") == stem("computers") == "computer"
    assert stem("dogs") == "dog" and stem("reptiles") == "reptile"
    # NO derivational stripping (prefixes or -ity/-ation/-ize/-ment).
    assert stem("organize") == "organize"
    assert stem("department") == "department"
    assert stem("unsafe") == "unsafe"  # un- is derivational; must NOT become 'safe'


def test_fused_detection() -> None:
    assert is_fused("me alert") and is_fused("me_alert") and is_fused("ground wet")
    assert not is_fused("duck") and not is_fused("penicillin")


def test_fused_rejected_even_with_taxonomic_verb() -> None:
    [claim] = parse_claims(json.dumps(
        [{"type": "RelationClaim", "subject": "x", "verb": "is_a", "object": "big dog"}]))
    assert validate_l0(claim, "x is a big dog").verdict == L0.REJECT


def test_l0_matches_corpus() -> None:
    cases = json.load(open(_CORPUS))["cases"]
    assert len(cases) == 18
    for c in cases:
        r = _l0_verdict(c)
        if c["decided_by"] == "L0":
            assert r.verdict.value == c["expect"], (c["id"], r.verdict.value, r.reason)
        else:  # L1/L2 cases: L0 is NOT the decider — it must DEFER, never accept/reject them.
            assert r.verdict == L0.DEFER, (c["id"], r.verdict.value, r.reason)


def test_back_render_templates_locked() -> None:
    assert back_render(_claim({"type": "RelationClaim", "subject": "tim", "verb": "is_a", "object": "duck"})) == "tim is a duck."
    assert back_render(_claim({"type": "NegatedRelationClaim", "subject": "dog", "verb": "is_a", "object": "reptile"})) == "dog is not a reptile."
    assert back_render(_claim({"type": "PropertyClaim", "subject": "sky", "value": "blue"})) == "sky is blue."
    assert back_render(_claim({"type": "NegatedPropertyClaim", "subject": "penicillin", "value": "safe"})) == "penicillin is not safe."
    # Relations: no verb conjugation, by design.
    assert back_render(_claim({"type": "RelationClaim", "subject": "coffee", "verb": "cause", "object": "alert"})) == "coffee cause alert."
    assert back_render(_claim({"type": "NegatedRelationClaim", "subject": "coffee", "verb": "cause", "object": "sleep"})) == "coffee does not cause sleep."


def test_l1_band_boundaries() -> None:
    assert l1_band(0.95) is Decision.COMMIT
    assert l1_band(0.90) is Decision.COMMIT          # accept is inclusive at 0.90
    assert l1_band(0.899) is Decision.ESCALATE
    assert l1_band(0.80) is Decision.ESCALATE         # escalate is inclusive at 0.80
    assert l1_band(0.7999) is Decision.REJECT
    assert l1_band(0.50) is Decision.REJECT


def test_l1_classifies_recorded_defer_cosines() -> None:
    # The empirical DEFER cosines must fall in the right bands (model-free; uses recorded reality).
    rec = json.load(open(_CORPUS))["_meta"]["live_reconciliation_2026-06-06"]["defer_cosines"]
    for cos in rec["synonym_accept"].values():
        assert l1_band(cos) is Decision.COMMIT, cos
    for cos in rec["hallucination_reject"].values():
        assert l1_band(cos) is Decision.REJECT, cos


def test_full_pipeline_and_human_escalation() -> None:
    # L0 short-circuits before any embedding (the FakeEmbedder is never consulted).
    g0 = IngestionGate(_FakeEmbedder({}))
    acc = _claim({"type": "RelationClaim", "subject": "Tim", "verb": "is_a", "object": "duck"})
    assert g0.evaluate(acc, "Tim is a duck.").decision is Decision.COMMIT
    fused = _claim({"type": "RelationClaim", "subject": "coffee", "verb": "makes", "object": "me alert"})
    r = g0.evaluate(fused, "Coffee makes me alert.")
    assert r.decision is Decision.REJECT and r.layer == "L0"

    # L1 bands via controlled vectors. Claim defers (atom 'zeta' absent from source).
    d = _claim({"type": "PropertyClaim", "subject": "alpha", "value": "zeta"})
    src, mirror = "alpha is gamma", "alpha is zeta."   # mirror == back_render(d)

    def gate(cos: float) -> IngestionGate:
        vb = [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]
        return IngestionGate(_FakeEmbedder({src: [1.0, 0.0], mirror: vb}))

    assert gate(1.00).evaluate(d, src).decision is Decision.COMMIT
    assert gate(0.50).evaluate(d, src).decision is Decision.REJECT
    esc = gate(0.85).evaluate(d, src)
    assert esc.decision is Decision.ESCALATE
    # The human-in-the-loop path carries the round-trip mirror for the [y/n] prompt, no exception.
    assert esc.layer == "L1" and esc.back_render == mirror and esc.cosine is not None
    for human_yes in (True, False):
        final = Decision.COMMIT if human_yes else Decision.REJECT  # console maps [y/n] -> outcome
        assert final in (Decision.COMMIT, Decision.REJECT)


def test_full_gate_against_corpus_with_live_embedder() -> None:
    # Model-gated: with the real embedder, the gate must COMMIT synonyms and REJECT hallucinations.
    if not os.environ.get("NARS_JARVIS_EMBED_GGUF"):
        print("SKIP: NARS_JARVIS_EMBED_GGUF unset"); return
    from language import LocalEmbedder
    gate = IngestionGate(LocalEmbedder())
    cases = json.load(open(_CORPUS))["cases"]
    for c in cases:
        res = gate.evaluate(_claim(c["claim"]), c["english"])
        want = {"accept": Decision.COMMIT, "reject": Decision.REJECT}[c["expect"]]
        assert res.decision is want, (c["id"], res.decision.value, res.reason)


if __name__ == "__main__":
    test_stem_is_inflectional_only()
    test_fused_detection()
    test_fused_rejected_even_with_taxonomic_verb()
    test_l0_matches_corpus()
    test_back_render_templates_locked()
    test_l1_band_boundaries()
    test_l1_classifies_recorded_defer_cosines()
    test_full_pipeline_and_human_escalation()
    test_full_gate_against_corpus_with_live_embedder()
    print("language/test_gate: OK")
