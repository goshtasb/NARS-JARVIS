"""Gate 2 / Stage 1 substrate: the L2 lexicon — deterministic exact + alias resolution (the bridge that
maps a query's surface mentions onto the graph's historical namespace before the embedder is consulted)."""
from retrieval.lexicon import LexiconStore


def _store(tmp_path):
    return LexiconStore(db_path=str(tmp_path / "lex.db"))


def test_exact_term_resolution(tmp_path):
    lx = _store(tmp_path)
    lx.record_term("solana", now=1.0)
    assert lx.resolve_exact("solana") == "solana"
    assert lx.resolve_exact("Solana") == "solana"          # lookups are atom()-normalized
    assert lx.resolve_exact("nonexistent") is None
    lx.close()


def test_alias_bridges_surface_to_canonical(tmp_path):
    lx = _store(tmp_path)
    # the deconstruction case: ingest saw "SOL", codified `solana`
    lx.record_alias("SOL", "solana", now=1.0)
    assert lx.resolve_alias("SOL") == ["solana"]
    assert lx.resolve_alias("sol") == ["solana"]           # normalized
    assert lx.resolve("sol") == "solana"                    # convenience: exact-miss -> top alias
    assert lx.term_count() == 1                             # recording the alias also registered the term
    lx.close()


def test_resolution_order_exact_beats_alias(tmp_path):
    lx = _store(tmp_path)
    lx.record_term("tx", now=1.0)                           # 'tx' exists as its own term
    lx.record_alias("tx", "transaction", now=1.0)          # ...and also aliases 'transaction'
    assert lx.resolve("tx") == "tx"                         # exact term wins over the alias
    lx.close()


def test_ambiguous_alias_ranked_by_frequency(tmp_path):
    lx = _store(tmp_path)
    lx.record_alias("eth", "ethereum", now=1.0)
    lx.record_alias("eth", "ethernet", now=1.0)
    lx.record_alias("eth", "ethereum", now=2.0)            # ethereum seen twice -> ranks first
    assert lx.resolve_alias("eth") == ["ethereum", "ethernet"]
    assert lx.resolve("eth") == "ethereum"
    lx.close()


def test_unresolved_mention_returns_none_never_guesses(tmp_path):
    lx = _store(tmp_path)
    lx.record_term("solana", now=1.0)
    assert lx.resolve("zkrollup") is None                  # not in lexicon -> caller falls to the embedder
    assert lx.resolve_alias("zkrollup") == []
    lx.close()


def test_durability_across_reopen(tmp_path):
    path = str(tmp_path / "lex.db")
    lx = LexiconStore(db_path=path)
    lx.record_alias("SOL", "solana", now=1.0)
    lx.close()
    lx2 = LexiconStore(db_path=path)                        # L2 is durable: survives a daemon restart
    assert lx2.resolve("SOL") == "solana"
    lx2.close()


def test_self_alias_is_ignored(tmp_path):
    lx = _store(tmp_path)
    lx.record_alias("solana", "solana", now=1.0)           # an alias equal to its term carries no signal
    assert lx.resolve_alias("solana") == []
    assert lx.resolve_exact("solana") == "solana"          # but the term itself is registered
    lx.close()
