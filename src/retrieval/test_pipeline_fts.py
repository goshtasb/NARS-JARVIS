"""Gate 2 / Sprint 1: the >4,000 factual crucible. Proves the FTS5 term-scoped fetch eliminated the
silent forgetting cliff — a relevant fact planted OLDEST survives a flood of newer noise and still grounds
through the full live pipeline (Stage 0-4, real ONA). Pre-cutover (facts_for_reload ORDER BY recency LIMIT
4000) the old facts would sit beyond position 4000 and be invisible -> false abstention."""
from brain import Brain
from memory.store import MemoryStore
from retrieval.lexicon import LexiconStore
from retrieval.pipeline import recall


def test_no_4k_cliff_oldest_relevant_fact_still_grounds(tmp_path):
    store = MemoryStore(str(tmp_path / "j.db"))
    # the load-bearing chain, planted FIRST = oldest / least-recently-used (recency cap would evict it)
    store.upsert("<solana --> timeout>", 1.0, 0.9, now=1.0)
    store.upsert("<timeout --> dropped_tx>", 1.0, 0.9, now=2.0)
    # saturate well past the old 4,000 barrier with NEWER, unrelated noise
    for i in range(5000):
        store.upsert(f"<noise{i} --> junk{i}>", 1.0, 0.9, now=100.0 + i)
    assert store.count() == 5002

    lex = LexiconStore(str(tmp_path / "lex.db"))   # empty lexicon -> anchors fall back to their atoms
    res = recall("Why did Solana cause dropped_tx?", store=store, lexicon=lex,
                 brain_factory=lambda: Brain(cycles_per_step=300), now=1e9)

    assert res.grounded, res                        # retrieved by TERM, not recency -> not forgotten
    assert res.answer == "<solana --> dropped_tx>", res
    assert {p["narsese"] for p in res.provenance} == {"<solana --> timeout>", "<timeout --> dropped_tx>"}
    store.close(); lex.close()


def test_fts_fetch_is_term_scoped_not_whole_store(tmp_path):
    # the candidate fetch returns ONLY facts sharing the queried atoms, regardless of total store size
    store = MemoryStore(str(tmp_path / "j.db"))
    store.upsert("<solana --> timeout>", 1.0, 0.9)
    for i in range(5000):
        store.upsert(f"<noise{i} --> junk{i}>", 1.0, 0.9)
    hits = store.facts_matching(["solana"])
    assert [f.narsese for f in hits] == ["<solana --> timeout>"]     # 1 of 5001, by term
    assert store.facts_matching(["timeout"])[0].narsese == "<solana --> timeout>"
    assert store.facts_matching(["nonexistent_atom"]) == []
    # compound atoms stay whole: 'timeout' must NOT match 'transaction_timeout'
    store.upsert("<x --> transaction_timeout>", 1.0, 0.9)
    assert {f.narsese for f in store.facts_matching(["timeout"])} == {"<solana --> timeout>"}
    assert store.facts_matching(["transaction_timeout"])[0].narsese == "<x --> transaction_timeout>"
    store.close()
