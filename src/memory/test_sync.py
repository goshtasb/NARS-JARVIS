"""Tests for L1<->L2 sync. parse_concepts/observe are pure; the reload test drives real ONA."""
from brain import Brain
from memory.store import MemoryStore
from memory.sync import observe, parse_concepts, reload_into_brain


def test_observe_persists_derivation() -> None:
    store = MemoryStore()
    lines = [
        "Derived: <a --> c>. creationTime=2 Stamp=[2,1] Truth: frequency=1.000000, confidence=0.810000",
        "Input: <a --> b>.",  # ignored (not Derived/Revised)
    ]
    assert observe(store, lines, now=5.0) == 1
    fact = store.get("<a --> c>")
    assert fact is not None and abs(fact.confidence - 0.81) < 1e-6


def test_parse_concepts_real_format() -> None:
    line = (
        '//<a --> b>: { "priority": 0.0, "usefulness": 0.99, "useCount": 7, '
        '"lastUsed": 2, "frequency": 1.0, "confidence": 0.9, "termlinks": ["a","b"]}'
    )
    assert parse_concepts([line]) == [("<a --> b>", 7, 2, 1.0, 0.9)]


def test_cache_miss_reload_end_to_end() -> None:
    # Persist to L2, then reload into a FRESH ONA (empty L1) and confirm it still reasons.
    store = MemoryStore()
    store.upsert("<tim --> duck>", 1.0, 0.9, now=1.0)
    store.upsert("<duck --> bird>", 1.0, 0.9, now=1.0)
    with Brain(cycles_per_step=100) as brain:
        assert reload_into_brain(store, brain, limit=40) == 2
        answer = brain.ask("<tim --> bird>?")
    assert answer is not None and answer.term == "<tim --> bird>", answer


if __name__ == "__main__":
    test_observe_persists_derivation()
    test_parse_concepts_real_format()
    test_cache_miss_reload_end_to_end()
    print("memory/test_sync: OK")
