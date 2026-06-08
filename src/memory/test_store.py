"""Unit tests for the SQLite system-of-record (in-memory DB; no ONA needed)."""
from memory.store import MemoryStore


def test_upsert_and_get() -> None:
    store = MemoryStore()
    store.upsert("<tim --> duck>", 1.0, 0.9, english="Tim is a duck", now=100.0)
    fact = store.get("<tim --> duck>")
    assert fact is not None
    assert fact.frequency == 1.0 and fact.confidence == 0.9
    assert fact.english == "Tim is a duck" and fact.use_count == 1


def test_upsert_revises_truth_and_keeps_english() -> None:
    store = MemoryStore()
    store.upsert("<x --> y>", 1.0, 0.9, english="orig", now=1.0)
    store.upsert("<x --> y>", 1.0, 0.95, now=2.0)  # revision, no english supplied
    fact = store.get("<x --> y>")
    assert fact is not None
    assert fact.confidence == 0.95 and fact.english == "orig" and fact.use_count == 2


def test_pinning_immune_to_prune() -> None:
    store = MemoryStore()
    for i in range(5):
        store.upsert(f"<a{i} --> b>", 1.0, 0.5, now=float(i))
    store.upsert("<self --> [allergic]>", 1.0, 0.9, english="I am allergic to penicillin", now=10.0)
    store.pin("<self --> [allergic]>")
    removed = store.prune(max_rows=3)  # 6 rows -> keep 3
    assert removed == 3
    assert store.get("<self --> [allergic]>") is not None  # pinned survives
    assert store.count() == 3


def test_embedding_roundtrip() -> None:
    store = MemoryStore()
    store.upsert("<a --> b>", 1.0, 0.9, embedding=[0.1, 0.2, 0.3], now=1.0)
    fact = store.get("<a --> b>")
    assert fact is not None and fact.embedding is not None
    assert len(fact.embedding) == 3 and abs(fact.embedding[0] - 0.1) < 1e-6


# ── conversational memory (ADR-008) ───────────────────────────────────────────────────
def test_remember_and_recall_roundtrip() -> None:
    store = MemoryStore()
    store.remember("the user's name is Ashkan", source="my name is Ashkan", now=1.0)
    store.remember("the user prefers dark mode", now=2.0)
    recalled = store.memories_for_recall()
    assert "the user's name is Ashkan" in recalled
    assert "the user prefers dark mode" in recalled


def test_remember_dedups_exact_text() -> None:
    store = MemoryStore()
    assert store.remember("the user is a pilot", now=1.0) is True   # newly created
    assert store.remember("the user is a pilot", now=2.0) is False  # already known -> bump only
    rows = store.memories_for_recall()
    assert rows.count("the user is a pilot") == 1
    count = store._db.execute("SELECT use_count FROM memories WHERE text=?",
                              ("the user is a pilot",)).fetchone()[0]
    assert count == 2


def test_forget_removes_memory() -> None:
    store = MemoryStore()
    store.remember("the user dislikes cilantro", now=1.0)
    assert store.forget("the user dislikes cilantro") == 1
    assert store.memories_for_recall() == []
    assert store.forget("nonexistent") == 0


def test_memories_coexist_with_facts() -> None:
    store = MemoryStore()
    store.upsert("<tim --> duck>", 1.0, 0.9, english="Tim is a duck", now=1.0)
    store.remember("the user's name is Ashkan", now=1.0)
    assert store.count() == 1                                   # facts table unaffected
    assert store.get("<tim --> duck>") is not None
    assert store.memories_for_recall() == ["the user's name is Ashkan"]


if __name__ == "__main__":
    test_upsert_and_get()
    test_upsert_revises_truth_and_keeps_english()
    test_pinning_immune_to_prune()
    test_embedding_roundtrip()
    test_remember_and_recall_roundtrip()
    test_remember_dedups_exact_text()
    test_forget_removes_memory()
    test_memories_coexist_with_facts()
    print("memory/test_store: OK")
