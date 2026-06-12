"""Gate 2.2 — the Crucible. Run the WHOLE retrieval pipeline (Stage 2 traversal -> Stage 3 ranking ->
Stage 4 live-ONA STAMP) against the planted adversarial corpus and prove the Alpha Gate:

    Recall (load-bearing in top_k) >= 85%   AND   False-Ground Rate == 0.00%.

Uses a REAL OpenNARS derivation for Stage 4 (not a mock) — the stamp is ONA's own evidential base.
"""
from retrieval.eval_corpus_adversarial import NOW, CASES
from retrieval.ranking import select
from retrieval.stage4_stamp import false_ground, stamp_provenance
from retrieval.traversal import BeliefGraph


def _run(case):
    graph = BeliefGraph(case.beliefs)
    top_k = select(graph, case.anchors, now=NOW, top_k=12)
    top_terms = {b.narsese for b in top_k}
    recall = len(case.load_bearing & top_terms) / len(case.load_bearing)
    answer, provenance = stamp_provenance(top_k, case.question)
    fg = false_ground(provenance, case.decoys, case.distractors)
    return top_terms, recall, answer, provenance, fg


def test_distractors_never_retrieved():
    # high-confidence, brand-new, but structurally unrelated -> proximity gates them before ranking.
    for case in CASES:
        top_terms, *_ = _run(case)
        assert not (case.distractors & top_terms), f"{case.name}: a distractor was retrieved: {case.distractors & top_terms}"


def test_recall_meets_alpha_target():
    for case in CASES:
        _, recall, *_ = _run(case)
        assert recall >= 0.85, f"{case.name}: derivation recall {recall:.0%} < 85%"


def test_iron_gate_zero_false_ground():
    for case in CASES:
        top_terms, recall, answer, provenance, fg = _run(case)
        print(f"\n[crucible {case.name}]")
        print(f"  retrieved top_k : {sorted(top_terms)}")
        print(f"  recall          : {recall:.0%}")
        print(f"  ONA answer      : {answer.term if answer else None}  truth={answer.truth if answer else None}")
        print(f"  STAMP provenance: {provenance}")
        print(f"  decoys retrieved: {sorted(case.decoys & top_terms)}  (in top_k but must NOT be cited)")
        print(f"  FALSE-GROUND    : {fg or 'none'}")
        assert answer is not None, f"{case.name}: ONA produced no answer"
        # THE IRON GATE: not one decoy/distractor in the stamp, even though decoys WERE retrieved.
        assert fg == set(), f"{case.name}: FALSE GROUND -> {fg}"
        # ...and the load-bearing chain IS the provenance (the answer really derived from it).
        assert case.load_bearing <= set(provenance), f"{case.name}: load-bearing missing from stamp: {case.load_bearing - set(provenance)}"


def test_decoys_are_retrieved_but_excluded_by_the_stamp():
    # the architecture's whole thesis in one assertion: loose retrieval, exact provenance.
    for case in CASES:
        top_terms, _, _, provenance, _ = _run(case)
        retrieved_decoys = case.decoys & top_terms
        assert retrieved_decoys, f"{case.name}: test is vacuous — no decoy was even retrieved"
        assert not (retrieved_decoys & set(provenance)), "a retrieved decoy leaked into the stamp"
