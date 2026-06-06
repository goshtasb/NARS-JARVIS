"""Persistent grounding: the entity-resolution cache survives a restart, and a known surface form
NEVER pays the embedder twice. Model-free — a call-counting embedder with controlled vectors and
the real SqliteGroundingStore on a temp file.
"""
import os
import tempfile

from language import Translator
from memory import SqliteGroundingStore


class _LLM:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return "[]"  # unused: we exercise _ground_atom directly


class _CountingEmbedder:
    def __init__(self, vectors: dict): self.calls = 0; self._v = vectors
    def embed(self, text: str):
        self.calls += 1
        return self._v[text]


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); return path


def test_synonym_alias_persists_with_zero_embed_after_restart() -> None:
    # car/automobile: the stemmer can't shortcut, so the embed is genuinely FORCED on first contact.
    vecs = {"car": [1.0, 0.0], "automobile": [0.9, (1.0 - 0.81) ** 0.5]}  # cosine 0.9
    db = _tmp_db()
    try:
        emb1 = _CountingEmbedder(vecs)
        store1 = SqliteGroundingStore(db)
        tr1 = Translator(_LLM(), embedder=emb1, cache=store1)
        assert tr1._ground_atom("car") == "car"            # new canonical (embed #1)
        assert tr1._ground_atom("automobile") == "car"     # nearest -> car, alias (embed #2)
        assert emb1.calls == 2, emb1.calls
        store1.close()

        # RESTART: brand-new store on the SAME file, brand-new embedder.
        emb2 = _CountingEmbedder(vecs)
        store2 = SqliteGroundingStore(db)
        tr2 = Translator(_LLM(), embedder=emb2, cache=store2)
        assert tr2._ground_atom("automobile") == "car"     # alias hit from disk
        assert emb2.calls == 0, f"embedder ran {emb2.calls}x after restart — cache not persisted"
        store2.close()
    finally:
        os.path.exists(db) and os.remove(db)


def test_plural_resolves_via_stemmer_then_persists() -> None:
    vecs = {"duck": [0.0, 1.0]}  # only 'duck' ever embeds
    db = _tmp_db()
    try:
        emb = _CountingEmbedder(vecs)
        store = SqliteGroundingStore(db)
        tr = Translator(_LLM(), embedder=emb, cache=store)
        assert tr._ground_atom("duck") == "duck"           # embed #1, new canonical
        assert tr._ground_atom("ducks") == "duck"          # stemmer fast-path -> duck, NO embed
        assert emb.calls == 1, emb.calls
        store.close()

        emb2 = _CountingEmbedder(vecs)
        store2 = SqliteGroundingStore(db)
        tr2 = Translator(_LLM(), embedder=emb2, cache=store2)
        assert tr2._ground_atom("ducks") == "duck"         # alias hit from disk
        assert emb2.calls == 0
        store2.close()
    finally:
        os.path.exists(db) and os.remove(db)


def test_store_normalizes_at_the_boundary() -> None:
    import numpy as np
    store = SqliteGroundingStore(":memory:")
    store.add_atom("x", [3.0, 4.0])                          # norm 5 -> stored as [0.6, 0.8]
    assert store.nearest([3.0, 4.0], 0.99) == "x"           # query also normalized -> cosine 1.0
    assert store.nearest([0.0, -1.0], 0.5) is None          # cosine -0.8 < 0.5
    blob = store._db.execute("SELECT embedding FROM atoms WHERE name='x'").fetchone()[0]
    v = np.frombuffer(blob, dtype=np.float32)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5, "stored vector is not unit length"
    store.close()


def test_bulk_load_rebuilds_matrix_across_instances() -> None:
    db = _tmp_db()
    try:
        s1 = SqliteGroundingStore(db)
        s1.add_atom("alpha", [1.0, 0.0]); s1.add_atom("beta", [0.0, 1.0])
        s1.add_alias("alphas", "alpha")
        assert s1.counts() == (2, 1)
        s1.close()
        s2 = SqliteGroundingStore(db)                        # fresh: lazy bulk-load from disk
        assert s2.resolve_surface("alphas") == "alpha"       # alias survived
        assert s2.resolve_surface("beta") == "beta"          # canonical survived
        assert s2.nearest([0.99, 0.0], 0.9) == "alpha"       # matrix rebuilt correctly
        assert s2.counts() == (2, 1)
        s2.close()
    finally:
        os.path.exists(db) and os.remove(db)


if __name__ == "__main__":
    test_synonym_alias_persists_with_zero_embed_after_restart()
    test_plural_resolves_via_stemmer_then_persists()
    test_store_normalizes_at_the_boundary()
    test_bulk_load_rebuilds_matrix_across_instances()
    print("test_grounding_persistence: OK")
