"""L0 structural gate — verified against validation_corpus.json + unit checks for stem/fused."""
import json
from pathlib import Path

from language.gate import L0, is_fused, stem, validate_l0
from language.schema import parse_claims

_CORPUS = Path(__file__).resolve().parent / "validation_corpus.json"


def _l0_verdict(case: dict):
    [claim] = parse_claims(json.dumps([case["claim"]]))
    return validate_l0(claim, case["english"])


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


if __name__ == "__main__":
    test_stem_is_inflectional_only()
    test_fused_detection()
    test_fused_rejected_even_with_taxonomic_verb()
    test_l0_matches_corpus()
    print("language/test_gate: OK")
