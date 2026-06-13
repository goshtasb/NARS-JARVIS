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


def test_migration_from_pre_adr009_schema(tmp_path) -> None:
    # Regression: an EXISTING ADR-008 `memories` table (no active/superseded_* columns). Opening
    # MemoryStore on it must migrate cleanly — not crash creating an index on a not-yet-added column.
    import sqlite3
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, source TEXT, "
        "embedding BLOB, pinned INTEGER NOT NULL DEFAULT 0, use_count INTEGER NOT NULL DEFAULT 1, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used REAL NOT NULL);"
        "CREATE UNIQUE INDEX idx_memories_text ON memories(text);"
        "INSERT INTO memories(text, created_at, updated_at, last_used) VALUES ('old fact',1,1,1);")
    con.commit(); con.close()
    store = MemoryStore(db)                                    # must not raise
    cols = {r[1] for r in store._db.execute("PRAGMA table_info(memories)")}
    assert {"active", "superseded_by", "superseded_at"} <= cols
    assert "old fact" in store.memories_for_recall()          # legacy row survives + is active
    store.close()


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
    # (pytest-only: test_migration_* take a tmp_path fixture)
    test_supersedence_chain_one_hop_semantics()
    print("memory/test_store: OK")


# ── v1.24.0 Step 1: the provenance (`source`) column migration ──
def test_fresh_db_has_source_column_defaulting_null() -> None:
    s = MemoryStore()
    s.upsert("<tim --> duck>", 1.0, 0.9, english="Tim is a duck")
    row = s._db.execute("SELECT source FROM facts WHERE narsese='<tim --> duck>'").fetchone()
    assert row == (None,)                                  # column exists; not yet written (Step 2 does that)
    s.close()


def test_pre_migration_db_upgrades_without_data_loss(tmp_path) -> None:
    """A jarvis.db created BEFORE the source column: opening it must add the column (O(1)), keep every
    existing row, leave them source=NULL (legacy/trusted), and not break the FTS index."""
    import dbconn
    path = str(tmp_path / "legacy.db")
    db = dbconn.connect(path)                               # build an OLD facts table — no `source`, no FTS
    db.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY, narsese TEXT NOT NULL UNIQUE, english TEXT,
        frequency REAL NOT NULL, confidence REAL NOT NULL, embedding BLOB,
        pinned INTEGER NOT NULL DEFAULT 0, priority_tier INTEGER NOT NULL DEFAULT 0,
        use_count INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, last_used REAL NOT NULL)""")
    db.execute("INSERT INTO facts(narsese,english,frequency,confidence,created_at,updated_at,last_used) "
               "VALUES ('<solana --> blockchain>','Solana is a blockchain',1.0,0.9,0,0,0)")
    db.commit(); db.close()

    s = MemoryStore(path)                                  # _migrate adds source + builds/backfills FTS
    row = s._db.execute("SELECT english, source FROM facts WHERE narsese='<solana --> blockchain>'").fetchone()
    assert row == ("Solana is a blockchain", None)         # legacy row survives, source = NULL (trusted)
    s.upsert("<bitcoin --> crypto>", 1.0, 0.9, english="Bitcoin is crypto")   # new write under new schema
    assert {f.narsese for f in s.facts_matching(["solana"])} >= {"<solana --> blockchain>"}   # FTS over legacy
    assert {f.narsese for f in s.facts_matching(["bitcoin"])} >= {"<bitcoin --> crypto>"}      # FTS over new
    s.close()


def test_migration_is_idempotent(tmp_path) -> None:
    path = str(tmp_path / "j.db")
    s1 = MemoryStore(path); s1.upsert("<a --> b>", 1.0, 0.9); s1.close()
    s2 = MemoryStore(path)                                  # re-open: _migrate re-runs, source already there -> no-op
    assert s2.get("<a --> b>") is not None                  # data intact, no crash-loop
    assert s2._db.execute("SELECT count(*) FROM pragma_table_info('facts') WHERE name='source'").fetchone()[0] == 1
    s2.close()


# ── v1.24.0 Step 3: the Value ranker + the passive decay sweep ──
_DAY = 86400.0


def _active_flag(store: MemoryStore, term: str):
    return store._db.execute("SELECT active FROM facts WHERE narsese=?", (term,)).fetchone()[0]


def test_ranker_orders_trusted_above_passive_and_excludes_tombstoned() -> None:
    s = MemoryStore()
    # Same confidence + recency: only source authority differs, so the trusted belief must outrank passive.
    s.upsert("<trusted --> a>", 1.0, 0.5, now=100.0)                      # source NULL -> authority 1.0
    s.upsert("<passive --> b>", 1.0, 0.5, now=100.0, source="passive")    # authority 0.4
    s.upsert("<gone --> c>", 1.0, 0.9, now=100.0, source="passive")
    s._db.execute("UPDATE facts SET active=0 WHERE narsese='<gone --> c>'")  # tombstoned
    s._db.commit()
    ranked = [f.narsese for f in s.facts_for_reload(now=100.0)]
    assert ranked[0] == "<trusted --> a>", ranked            # higher authority wins the top slot
    assert "<passive --> b>" in ranked
    assert "<gone --> c>" not in ranked, "tombstoned belief must never be reloaded"
    s.close()


def test_ranker_pinned_outranks_everything() -> None:
    s = MemoryStore()
    s.upsert("<trusted --> a>", 1.0, 0.9, now=100.0)
    s.upsert("<pinned --> b>", 1.0, 0.5, now=100.0, source="passive")     # low V, but pinned
    s.pin("<pinned --> b>")
    assert s.facts_for_reload(now=100.0)[0].narsese == "<pinned --> b>"
    s.close()


def test_sweep_tombstones_stale_unused_floor_passive() -> None:
    s = MemoryStore()
    s.upsert("<stale --> p>", 1.0, 0.5, now=0.0, source="passive")        # floor, unused, will age out
    n = s.sweep_passive(now=31 * _DAY)                                    # 31 days later
    assert n == 1
    assert _active_flag(s, "<stale --> p>") == 0                          # tombstoned (soft)
    assert s.get("<stale --> p>") is not None                            # but physically PRESENT (reversible)
    s.close()


def test_sweep_spares_recalled_corroborated_recent_pinned_and_trusted() -> None:
    s = MemoryStore()
    # (1) recalled/corroborated: a second upsert bumps use_count past 1 -> spared by the use_count gate.
    s.upsert("<recalled --> p>", 1.0, 0.5, now=0.0, source="passive")
    s.upsert("<recalled --> p>", 1.0, 0.5, now=0.0, source="passive")    # use_count=2, still aged (last_used=0)
    # (2) high-confidence passive: V backstop spares it even unused (use_count=1).
    s.upsert("<strong --> p>", 1.0, 0.95, now=0.0, source="passive")
    # (3) recent passive: inside the 30-day grace window.
    s.upsert("<recent --> p>", 1.0, 0.5, now=30 * _DAY, source="passive")
    # (4) pinned passive at the floor.
    s.upsert("<pinned --> p>", 1.0, 0.5, now=0.0, source="passive"); s.pin("<pinned --> p>")
    # (5) trusted (source NULL) at the floor, unused — never a sweep target.
    s.upsert("<trusted --> t>", 1.0, 0.5, now=0.0)

    n = s.sweep_passive(now=31 * _DAY)
    assert n == 0, "sweep struck a protected belief"
    for term in ("<recalled --> p>", "<strong --> p>", "<recent --> p>", "<pinned --> p>", "<trusted --> t>"):
        assert _active_flag(s, term) == 1, f"{term} was wrongly tombstoned"
    s.close()


def test_sweep_is_idempotent_and_skips_already_tombstoned() -> None:
    s = MemoryStore()
    s.upsert("<stale --> p>", 1.0, 0.5, now=0.0, source="passive")
    assert s.sweep_passive(now=31 * _DAY) == 1
    assert s.sweep_passive(now=40 * _DAY) == 0, "re-sweep must not re-tombstone (active=1 guard)"
    s.close()


def test_pre_step3_db_gains_active_column_with_existing_rows_live(tmp_path) -> None:
    """A jarvis.db created AFTER Step 1 (has `source`) but BEFORE Step 3 (no `active`): opening it must add
    `active` NOT NULL DEFAULT 1, so every existing row is live and immediately reloadable/recallable."""
    import dbconn
    path = str(tmp_path / "pre_step3.db")
    db = dbconn.connect(path)
    db.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY, narsese TEXT NOT NULL UNIQUE, english TEXT, source TEXT,
        frequency REAL NOT NULL, confidence REAL NOT NULL, embedding BLOB,
        pinned INTEGER NOT NULL DEFAULT 0, priority_tier INTEGER NOT NULL DEFAULT 0,
        use_count INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, last_used REAL NOT NULL)""")
    db.execute("INSERT INTO facts(narsese,english,frequency,confidence,created_at,updated_at,last_used) "
               "VALUES ('<eth --> blockchain>','ETH is a blockchain',1.0,0.9,0,0,0)")
    db.commit(); db.close()

    s = MemoryStore(path)
    assert _active_flag(s, "<eth --> blockchain>") == 1                   # existing row is live
    assert {f.narsese for f in s.facts_for_reload()} >= {"<eth --> blockchain>"}   # reloadable
    assert {f.narsese for f in s.facts_matching(["eth"])} >= {"<eth --> blockchain>"}  # recallable
    s.close()
