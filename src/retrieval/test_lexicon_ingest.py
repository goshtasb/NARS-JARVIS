"""Gate 2: ingest -> lexicon population. Term extraction from committed Narsese + alias harvesting."""
from retrieval.lexicon import LexiconStore
from retrieval.lexicon_ingest import record_alias_pairs, record_narsese_terms, terms_in_narsese


def test_terms_extracted_from_simple_belief():
    assert terms_in_narsese("<solana --> blockchain>.") == ["solana", "blockchain"]


def test_terms_extracted_from_compound_and_truth_value():
    # a relation with a compound subject + an explicit truth value (digits must NOT leak as terms)
    assert terms_in_narsese("<(solana * timeout) --> has_event>. {0.0 0.9}") == ["solana", "timeout", "has_event"]


def test_property_belief_terms():
    assert terms_in_narsese("<cpu --> [pegged]>.") == ["cpu", "pegged"]


def test_dedup_preserves_order():
    assert terms_in_narsese("<solana --> solana>.") == ["solana"]


def test_record_narsese_terms_leaves_a_footprint(tmp_path):
    lx = LexiconStore(db_path=str(tmp_path / "lex.db"))
    assert lx.term_count() == 0
    recorded = record_narsese_terms(lx, "<solana --> blockchain>.", now=1.0)
    assert recorded == ["solana", "blockchain"]
    assert lx.term_count() == 2
    assert lx.resolve_exact("solana") == "solana" and lx.resolve_exact("blockchain") == "blockchain"
    lx.close()


def test_record_alias_pairs(tmp_path):
    lx = LexiconStore(db_path=str(tmp_path / "lex.db"))
    n = record_alias_pairs(lx, [{"surface": "SOL", "canonical": "solana"},
                                {"surface": "", "canonical": "x"},          # malformed -> skipped
                                {"surface": "BTC", "canonical": "bitcoin"}], now=1.0)
    assert n == 2
    assert lx.resolve("SOL") == "solana" and lx.resolve("BTC") == "bitcoin"
    lx.close()


def test_alias_pairs_tolerates_garbage(tmp_path):
    lx = LexiconStore(db_path=str(tmp_path / "lex.db"))
    assert record_alias_pairs(lx, None, now=1.0) == 0
    assert record_alias_pairs(lx, ["not a dict", {"surface": "a"}], now=1.0) == 0   # no canonical -> skip
    lx.close()
