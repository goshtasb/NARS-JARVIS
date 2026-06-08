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


# ── ADR-009: ranked retrieval, supersedence, chain semantics ───────────────────────────
def _emb(*xs: float) -> list[float]:
    return list(xs)


def test_search_ranks_by_cosine_over_active() -> None:
    store = MemoryStore()
    store.remember("the user lives in Berlin", embedding=_emb(1.0, 0.0, 0.0))
    store.remember("the user likes tea", embedding=_emb(0.0, 1.0, 0.0))
    store.remember("the user uses vim", embedding=_emb(0.0, 0.0, 1.0))
    top = store.search(_emb(0.9, 0.1, 0.0), k=1)
    assert top == ["the user lives in Berlin"]               # nearest, not most-recent


def test_supersede_on_single_valued_slot_conflict() -> None:
    store = MemoryStore()
    store.remember("the user's name is Ashkan", embedding=_emb(1.0, 0.0))
    store.remember("the user's name is Sam", embedding=_emb(1.0, 0.0))   # same slot, new value
    recalled = store.memories_for_recall()
    assert "the user's name is Sam" in recalled
    assert "the user's name is Ashkan" not in recalled       # old tombstoned
    assert store.search(_emb(1.0, 0.0)) == ["the user's name is Sam"]


def test_no_supersede_for_multivalued() -> None:
    store = MemoryStore()
    store.remember("the user likes tea", embedding=_emb(1.0, 0.0))
    store.remember("the user likes coffee", embedding=_emb(1.0, 0.0))
    recalled = store.memories_for_recall()
    assert "the user likes tea" in recalled and "the user likes coffee" in recalled  # both kept


def test_forget_is_soft_and_restorable() -> None:
    store = MemoryStore()
    store.remember("the user likes tea")
    assert store.forget("the user likes tea") == 1
    assert store.memories_for_recall() == []                 # tombstoned, not in recall
    assert store.restore("the user likes tea") is True
    assert "the user likes tea" in store.memories_for_recall()
    assert store.forget("nonexistent") == 0


def test_forget_normalized_matches_case_and_punctuation() -> None:
    store = MemoryStore()
    store.remember("the user likes tea")
    assert store.forget_normalized("The user likes tea.") == 1
    assert "the user likes tea" not in store.memories_for_recall()
    assert store.forget_normalized("the user likes coffee") == 0   # no false match


def _active(store: MemoryStore) -> set[str]:
    return set(store.memories_for_recall(limit=999))


def test_supersedence_chain_one_hop_semantics() -> None:
    # Build A <- B <- C on the indentation slot.
    store = MemoryStore()
    store.remember("the user prefers spaces over tabs", embedding=_emb(1.0, 0.0))  # A
    store.remember("the user prefers tabs over spaces", embedding=_emb(1.0, 0.0))  # B supersedes A
    # C: a third value on the same slot. (Use the editor-free indentation phrasing differently.)
    store.remember("the user prefers spaces over tabs and 2-space indents",
                   embedding=_emb(1.0, 0.0))                                        # C supersedes B
    assert _active(store) == {"the user prefers spaces over tabs and 2-space indents"}

    # undo C -> reactivate its immediate predecessor (B), A stays tombstoned, C tombstoned.
    assert store.undo_supersede("the user prefers spaces over tabs and 2-space indents") is True
    assert _active(store) == {"the user prefers tabs over spaces"}                  # B only

    # forget B -> slot empty, A NOT auto-restored.
    assert store.forget("the user prefers tabs over spaces") == 1
    assert _active(store) == set()

    # explicit restore A -> A active again (nothing else holds the slot now).
    assert store.restore("the user prefers spaces over tabs") is True
    assert _active(store) == {"the user prefers spaces over tabs"}


def test_migration_idempotent_on_existing_db(tmp_path) -> None:
    # Open twice on a file DB: the second open must not crash re-adding columns.
    db = str(tmp_path / "m.db")
    s1 = MemoryStore(db); s1.remember("the user's name is Ashkan"); s1.close()
    s2 = MemoryStore(db)                                       # _migrate runs again, no-op
    assert "the user's name is Ashkan" in s2.memories_for_recall()
    cols = {r[1] for r in s2._db.execute("PRAGMA table_info(memories)")}
    assert {"active", "superseded_by", "superseded_at"} <= cols
    s2.close()


if __name__ == "__main__":
    test_upsert_and_get()
    test_upsert_revises_truth_and_keeps_english()
    test_pinning_immune_to_prune()
    test_embedding_roundtrip()
    test_remember_and_recall_roundtrip()
    test_remember_dedups_exact_text()
    test_forget_removes_memory()
    test_memories_coexist_with_facts()
    test_search_ranks_by_cosine_over_active()
    test_supersede_on_single_valued_slot_conflict()
    test_no_supersede_for_multivalued()
    test_forget_is_soft_and_restorable()
    test_forget_normalized_matches_case_and_punctuation()
    test_supersedence_chain_one_hop_semantics()
    print("memory/test_store: OK")
