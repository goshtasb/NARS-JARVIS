"""Gate 2 / Stage 0: tokenization + surface-mention extraction. Pure, model-free."""
from retrieval.query_parse import QueryMentions, extract_mentions, tokenize


def test_tokenize_keeps_tickers_paths_and_ids():
    assert tokenize("Why did my tx drop on SOL?") == ["Why", "did", "my", "tx", "drop", "on", "SOL"]
    assert tokenize("read ~/Desktop/report.pdf now") == ["read", "Desktop/report.pdf", "now"]
    assert tokenize("") == []


def test_the_solana_query_from_the_deconstruction():
    # the exact case Synapse pressed on: surface mentions, stopwords gone, SOL flagged as the anchor.
    q = extract_mentions("Why did my tx drop on SOL?")
    assert q.mentions == ["tx", "drop", "sol"]        # normalized content tokens, stopwords removed
    assert q.anchors == ["sol"]                       # the entity anchor that constrains Stage-1 search


def test_proper_nouns_and_lowercase_terms_split_correctly():
    q = extract_mentions("Solana mainnet transaction routing")
    assert q.mentions == ["solana", "mainnet", "transaction", "routing"]
    assert q.anchors == ["solana"]                    # Capitalized proper noun -> anchor; the rest are not


def test_anchor_detection_rules():
    q = extract_mentions("Did Chrome spike CPU on the M2 again")
    # Chrome (Proper), CPU (all-caps), M2 (caps+digit) are anchors; spike is a plain mention
    assert "chrome" in q.anchors and "cpu" in q.anchors and "m2" in q.anchors
    assert "spike" in q.mentions and "spike" not in q.anchors
    assert "did" not in q.mentions and "again" not in q.mentions   # stopwords dropped


def test_paths_and_dotted_symbols_split_into_anchor_components():
    q = extract_mentions("summarize ~/Desktop/PRD.pdf and check tx.routing")
    # compound entity tokens split into clean component mentions, each an anchor
    assert {"prd", "pdf", "desktop"} <= set(q.anchors)
    assert "tx" in q.anchors and "routing" in q.anchors
    assert "summarize" in q.mentions and "summarize" not in q.anchors   # plain verb stays a mention
    assert "and" not in q.mentions                                      # stopword dropped


def test_normalization_dedup_and_order():
    q = extract_mentions("Solana solana SOLANA tx!!! tx")
    assert q.mentions == ["solana", "tx"]               # atom()-normalized, de-duped, order-preserving


def test_empty_and_stopword_only_queries():
    assert extract_mentions("").mentions == []
    assert extract_mentions("why is it that the").mentions == []   # all stopwords -> no anchors, safe miss
    assert extract_mentions("why is it").anchors == []


def test_returns_query_mentions_shape():
    q = extract_mentions("ping SOL")
    assert isinstance(q, QueryMentions)
    assert q.tokens == ["ping", "SOL"] and q.mentions == ["ping", "sol"] and q.anchors == ["sol"]
