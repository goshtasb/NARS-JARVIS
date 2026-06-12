"""Gate 2 / Stages 2-3: deterministic graph traversal + truth ranking. Proves a precise top-k subgraph
is isolated from a NOISY database, ignoring distant beliefs that merely share surface vocabulary."""
from retrieval.ranking import _proximity, _recency, rank, score, select
from retrieval.traversal import Belief, BeliefGraph

DAY = 86400.0
NOW = 1_000_000.0


def _b(narsese, f=1.0, c=0.9, age_days=0.0):
    return Belief(narsese, f, c, NOW - age_days * DAY)


# A noisy world: a load-bearing Solana neighborhood, plus decoys that share surface vocab or are distant.
WORLD = [
    _b("<solana --> blockchain>"),                       # 1-hop from solana
    _b("<(solana * timeout) --> has_event>"),            # 1-hop (compound -> atoms solana, timeout)
    _b("<blockchain --> ledger>"),                       # 2-hop (via blockchain)
    _b("<solanaceae --> plant>"),                        # DECOY: 'solanaceae' != 'solana' (surface only)
    _b("<ledger --> accounting>"),                       # 3-hop (via blockchain->ledger) -> excluded at 2
    _b("<cursor --> editor>"),                           # distant domain, no shared atom
    _b("<editor --> tool>"),                             # distant domain
]


def test_traversal_isolates_the_anchor_neighborhood():
    g = BeliefGraph(WORLD)
    hits = {b.narsese: hop for b, hop in g.traverse(["solana"], max_hops=2)}
    assert hits["<solana --> blockchain>"] == 1
    assert hits["<(solana * timeout) --> has_event>"] == 1
    assert hits["<blockchain --> ledger>"] == 2
    # excluded: textual-only overlap, 3-hop, and distant domain
    assert "<solanaceae --> plant>" not in hits          # word-overlap != graph-adjacency
    assert "<ledger --> accounting>" not in hits          # 3-hop, beyond the budget
    assert "<cursor --> editor>" not in hits


def test_one_hop_only():
    g = BeliefGraph(WORLD)
    hits = {b.narsese for b, hop in g.traverse(["solana"], max_hops=1)}
    assert hits == {"<solana --> blockchain>", "<(solana * timeout) --> has_event>"}


def test_no_anchor_in_graph_abstains():
    g = BeliefGraph(WORLD)
    assert g.traverse(["zkrollup"], max_hops=2) == []     # honest abstention -> Stage 4 grounds nothing
    assert select(g, [], now=NOW) == []                   # no anchors at all


def test_proximity_and_recency_factors():
    assert _proximity(1) == 1.0 and _proximity(2) == 0.5
    assert _recency(NOW, NOW) == 1.0                      # brand new -> no penalty
    assert _recency(NOW - 90 * DAY, NOW) == 0.9           # saturates at the slight floor (10% max)
    assert 0.9 <= _recency(NOW - 45 * DAY, NOW) <= 1.0    # bounded -> a slight tie-breaker, never dominant
    # structural proximity dominates: a 1-hop fact outranks an equally-confident 2-hop fact
    assert score(_b("a", c=0.9), 1, now=NOW) > score(_b("b", c=0.9), 2, now=NOW)


def test_ranking_breaks_ties_by_recency():
    g = BeliefGraph([_b("<solana --> a>", c=0.8, age_days=0), _b("<solana --> b>", c=0.8, age_days=120)])
    ranked = select(g, ["solana"], now=NOW)
    assert ranked[0].narsese == "<solana --> a>"          # equal conf+hop -> the more recent wins


def test_hard_top_k_budget_keeps_the_strongest():
    # 15 one-hop beliefs of descending confidence; the budget keeps exactly the 12 strongest.
    world = [_b(f"<solana --> n{i:02d}>", c=round(0.99 - i * 0.05, 4)) for i in range(15)]
    g = BeliefGraph(world)
    ranked = select(g, ["solana"], now=NOW, top_k=12)
    assert len(ranked) == 12                              # the hard AIKR / context budget
    kept = {b.narsese for b in ranked}
    assert "<solana --> n00>" in kept                     # highest confidence kept
    assert "<solana --> n14>" not in kept                 # 3 weakest dropped
    assert "<solana --> n13>" not in kept and "<solana --> n12>" not in kept


def test_strong_1hop_beats_weak_2hop_under_budget():
    # a precise subgraph: a strong distant-ish fact must not crowd out the load-bearing close ones
    world = [
        _b("<solana --> blockchain>", c=0.95),           # 1-hop, strong
        _b("<blockchain --> ledger>", c=0.99),           # 2-hop, even stronger truth...
    ]
    g = BeliefGraph(world)
    ranked = select(g, ["solana"], now=NOW, top_k=1)      # budget of 1 forces the choice
    # 1-hop (0.95*1.0=0.95) edges out 2-hop (0.99*0.5=0.495): proximity dominates as designed
    assert ranked[0].narsese == "<solana --> blockchain>"
