"""Gate 2.2 / 2.3 — the Crucible. Run the WHOLE pipeline (Stage 2 traversal -> Stage 3 chain-aware
ranking -> Stage 4 live-ONA STAMP) against the adversarial matrix and prove the Alpha Gate per case:

    belief recall (load-bearing in top_k) >= 85%   AND   False-Ground Rate == 0.00%.

Stage 4 uses a REAL OpenNARS derivation — the stamp is ONA's own evidential base, not a mock."""
from retrieval.eval_corpus_adversarial import NOW, CASES, REVISION_CASE, SATURATED_CASE
from retrieval.ranking import select, select_grounded
from retrieval.stage4_stamp import false_ground, stamp_provenance
from retrieval.traversal import BeliefGraph
from memory.fact import to_statement


def _run(case, max_hops=4):
    graph = BeliefGraph(case.beliefs)
    top_k = select_grounded(graph, case.anchors, case.targets, now=NOW, max_hops=max_hops, top_k=12)
    top_terms = {b.narsese for b in top_k}
    recall = len(case.load_bearing & top_terms) / len(case.load_bearing)
    answer, provenance = stamp_provenance(top_k, case.question)
    fg = false_ground(provenance, case.decoys, case.distractors)
    return top_terms, recall, answer, provenance, fg


def test_matrix_alpha_gate():
    for case in CASES:
        top_terms, recall, answer, provenance, fg = _run(case)
        print(f"\n[crucible {case.name}]  q={case.query!r}")
        print(f"  top_k ({len(top_terms)}): recall={recall:.0%}  answer={answer.term if answer else None}")
        print(f"  STAMP provenance : {provenance}")
        print(f"  decoys retrieved : {sorted(case.decoys & top_terms)}")
        print(f"  FALSE-GROUND     : {fg or 'none'}")
        assert recall >= 0.85, f"{case.name}: belief recall {recall:.0%} < 85%"
        assert not (case.distractors & top_terms), f"{case.name}: distractor retrieved: {case.distractors & top_terms}"
        assert answer is not None, f"{case.name}: ONA produced no answer (chain did not derive)"
        assert fg == set(), f"{case.name}: FALSE GROUND -> {fg}"
        assert case.load_bearing <= set(provenance), \
            f"{case.name}: load-bearing missing from STAMP: {case.load_bearing - set(provenance)}"


def test_saturated_budget_naive_collapses_grounded_survives():
    """The fallback's reason to exist: 15 confident+recent 1-hop decoys vs a 4-hop chain."""
    case = SATURATED_CASE
    g = BeliefGraph(case.beliefs)
    naive = {b.narsese for b in select(g, case.anchors, now=NOW, max_hops=4, top_k=12)}
    grounded = {b.narsese for b in select_grounded(g, case.anchors, case.targets, now=NOW, max_hops=4, top_k=12)}
    naive_recall = len(case.load_bearing & naive) / len(case.load_bearing)
    grounded_recall = len(case.load_bearing & grounded) / len(case.load_bearing)
    print(f"\n[saturated] naive recall={naive_recall:.0%}  grounded recall={grounded_recall:.0%}")
    assert naive_recall < 0.5, f"naive should collapse, got {naive_recall:.0%}"   # chain evicted by the flood
    assert grounded_recall == 1.0                                                  # path-pinning saved it
    assert len(grounded) <= 12                                                     # budget still honored


def test_revision_reflects_day60_reality():
    """The Day-60 correction must win: querying the obsolete location returns a revised-down truth."""
    case = REVISION_CASE
    from brain import Brain
    b = Brain(cycles_per_step=300)
    try:
        for belief in case.beliefs:
            b.add_belief(to_statement(belief.narsese, belief.frequency, belief.confidence))
        in_config = b.ask("<api_key --> in_config>?")
        in_keychain = b.ask("<api_key --> in_keychain>?")
        print(f"\n[revision] in_config -> {in_config.truth if in_config else None}; "
              f"in_keychain -> {in_keychain.truth if in_keychain else None}")
        assert in_keychain is not None and in_keychain.truth.frequency > 0.5     # current reality holds
        # the obsolete belief was corrected: revising {1.0} with the {0.0} correction pulls it from
        # certain-true (1.0) down to maximally-uncertain (0.5) at HIGHER confidence -> no longer believed.
        assert in_config is not None and in_config.truth.frequency <= 0.5
        assert in_config.truth.confidence > 0.9                                   # revision added evidence
        assert in_keychain.truth.frequency > in_config.truth.frequency           # Day-60 reality wins
    finally:
        b.close()
