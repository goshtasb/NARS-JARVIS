"""Gate 2: the PRIVATE VAULT learns the user's vocabulary with no network.

When the local model extracts a claim mentioning a surface form (SOL) and on-device grounding resolves it
to a canonical concept (solana), that surface->canonical mapping must land in the L2 lexicon alias table —
so Stage 1 can resolve it later, even if the user never connects Cloud. No real model / embedder / network:
all collaborators are local fakes; the point is the PLUMBING (grounding -> alias_sink -> record_alias)."""
from language import Translator
from retrieval.lexicon import LexiconStore


class FakeLLM:
    """Stands in for the local 7B: returns a GBNF-shaped claim array mentioning the surface form 'SOL'."""
    def __init__(self, claims_json):
        self._json = claims_json
    def generate(self, system, sentence):
        return self._json


class FakeEmbedder:
    """On-device embedder fake: encodes the surface string itself so the fake cache can key on it.
    No network — embedding is a pure local op, which is the whole point of the proof."""
    def embed(self, s):
        return [[s]]


class FakeCache:
    """Grounding cache fake: maps a surface 'vector' (== [surface]) to a canonical concept."""
    def __init__(self, canon_map):
        self._canon = canon_map
        self.aliases: dict[str, str] = {}
        self.atoms: set[str] = set()
    def resolve_surface(self, s):
        return self.aliases.get(s)
    def nearest(self, vec, threshold):
        return self._canon.get(vec[0])          # vec is [surface]
    def add_alias(self, s, c):
        self.aliases[s] = c
    def add_atom(self, s, vec):
        self.atoms.add(s)


def _translator(lex, claims_json, canon_map):
    return Translator(
        FakeLLM(claims_json),
        embedder=FakeEmbedder(),
        cache=FakeCache(canon_map),
        alias_sink=lambda surface, canonical: lex.record_alias(surface, canonical, now=1.0),
    )


def test_local_grounding_harvests_alias_into_lexicon(tmp_path):
    lex = LexiconStore(db_path=str(tmp_path / "lex.db"))
    # local model says: "<SOL --> network_failure>" ; grounding knows SOL == canonical 'solana'
    claims = '[{"type":"RelationClaim","subject":"SOL","verb":"hit","object":"timeout"}]'
    tr = _translator(lex, claims, canon_map={"sol": "solana"})

    grounded = tr.claims("SOL hit a timeout")            # runs generate + parse + GROUND (no network)
    assert grounded[0].subject == "solana"               # grounding resolved the surface to canonical
    # ...and the surface->canonical mapping was harvested into the lexicon, queryable deterministically:
    assert lex.resolve("SOL") == "solana"
    assert lex.resolve_alias("sol") == ["solana"]
    lex.close()


def test_no_alias_when_surface_equals_canonical(tmp_path):
    lex = LexiconStore(db_path=str(tmp_path / "lex.db"))
    claims = '[{"type":"RelationClaim","subject":"solana","verb":"is","object":"blockchain"}]'
    tr = _translator(lex, claims, canon_map={"solana": "solana"})   # canonical resolves to itself
    tr.claims("solana is a blockchain")
    assert lex.resolve_alias("solana") == []             # surface == canonical -> no alias noise
    lex.close()


def test_harvest_is_offline_and_survives_without_cloud(tmp_path):
    # The proof Synapse asked for: a permanent alias footprint with zero network involvement.
    path = str(tmp_path / "lex.db")
    lex = LexiconStore(db_path=path)
    tr = _translator(lex, '[{"type":"PropertyClaim","subject":"BTC","value":"volatile"}]',
                     canon_map={"btc": "bitcoin"})
    tr.claims("BTC is volatile")
    lex.close()
    reopened = LexiconStore(db_path=path)                 # durable across restart, never touched a network
    assert reopened.resolve("BTC") == "bitcoin"
    reopened.close()
