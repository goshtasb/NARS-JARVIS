"""SQLite system-of-record (L2). Imperative Shell (S-02) — durable, pinnable.

ONA (L1) self-forgets to protect its 40-slot buffer; this store is the permanent safety net.
Pinned facts are immune to pruning. See PRD §6 and the approved sync model (write-through /
observe / snapshot / cache-miss reload). There is no eviction callback (ONA evicts silently);
durability comes from write-through at ingestion + reconciliation.
"""
from __future__ import annotations

import sqlite3
import time

from .fact import Fact, pack_embedding, unpack_embedding

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  narsese       TEXT    NOT NULL UNIQUE,
  english       TEXT,
  frequency     REAL    NOT NULL,
  confidence    REAL    NOT NULL,
  embedding     BLOB,
  pinned        INTEGER NOT NULL DEFAULT 0,
  priority_tier INTEGER NOT NULL DEFAULT 0,
  use_count     INTEGER NOT NULL DEFAULT 1,
  created_at    REAL    NOT NULL,
  updated_at    REAL    NOT NULL,
  last_used     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_pin ON facts(pinned, priority_tier);

-- Conversational memory (ADR-008): free-form English facts auto-extracted from chat. Kept
-- SEPARATE from `facts` so it carries no Narsese mirror constraint — anything the LLM decides
-- to remember (preferences, tasks, self-facts) is durable here even when it has no clean ONA
-- form. `_recall` merges this with `facts.english`. `text` is unique so identical saves dedup.
CREATE TABLE IF NOT EXISTS memories (
  id         INTEGER PRIMARY KEY,
  text       TEXT    NOT NULL,
  source     TEXT,
  embedding  BLOB,
  pinned     INTEGER NOT NULL DEFAULT 0,
  use_count  INTEGER NOT NULL DEFAULT 1,
  created_at REAL    NOT NULL,
  updated_at REAL    NOT NULL,
  last_used  REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_text ON memories(text);
"""

_COLS = (
    "narsese, english, frequency, confidence, embedding, pinned, "
    "priority_tier, use_count, created_at, updated_at, last_used"
)


def _row_to_fact(r: tuple) -> Fact:
    return Fact(r[0], r[1], r[2], r[3], unpack_embedding(r[4]), bool(r[5]),
                r[6], r[7], r[8], r[9], r[10])


class MemoryStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def upsert(self, narsese: str, frequency: float, confidence: float,
               english: str | None = None, embedding: list[float] | None = None,
               now: float | None = None) -> None:
        """Write-through (creation) AND observe (revision): insert or update truth in place.

        On conflict, truth is overwritten, english/embedding kept if the new value is None,
        use_count incremented, recency bumped. Pinning is managed separately via pin()/unpin().
        """
        now = time.time() if now is None else now
        self._db.execute(
            """INSERT INTO facts (narsese, english, frequency, confidence, embedding,
                                  use_count, created_at, updated_at, last_used)
               VALUES (?,?,?,?,?,1,?,?,?)
               ON CONFLICT(narsese) DO UPDATE SET
                 frequency=excluded.frequency,
                 confidence=excluded.confidence,
                 english=COALESCE(excluded.english, facts.english),
                 embedding=COALESCE(excluded.embedding, facts.embedding),
                 use_count=facts.use_count+1,
                 updated_at=excluded.updated_at,
                 last_used=excluded.last_used""",
            (narsese, english, frequency, confidence, pack_embedding(embedding), now, now, now),
        )
        self._db.commit()

    # ── conversational memory (ADR-008) — guaranteed-recall English store ──────────────
    def remember(self, text: str, source: str | None = None, now: float | None = None) -> bool:
        """Persist one auto-extracted English memory. Idempotent: identical text bumps usage/recency
        instead of duplicating (the UNIQUE index on `text` does exact-match dedup). Returns True iff
        this created a NEW memory (False if it was already known) — lets the caller acknowledge only
        genuinely new saves and stay silent when the user merely revisits a known fact."""
        now = time.time() if now is None else now
        is_new = self._db.execute("SELECT 1 FROM memories WHERE text=?", (text,)).fetchone() is None
        self._db.execute(
            """INSERT INTO memories (text, source, use_count, created_at, updated_at, last_used)
               VALUES (?,?,1,?,?,?)
               ON CONFLICT(text) DO UPDATE SET
                 source=COALESCE(excluded.source, memories.source),
                 use_count=memories.use_count+1,
                 updated_at=excluded.updated_at,
                 last_used=excluded.last_used""",
            (text, source, now, now, now),
        )
        self._db.commit()
        return is_new

    def memories_for_recall(self, limit: int = 30) -> list[str]:
        """English memories to inject as ground truth: pinned first, then most recently used."""
        rows = self._db.execute(
            "SELECT text FROM memories ORDER BY pinned DESC, last_used DESC LIMIT ?",
            (limit,)).fetchall()
        return [r[0] for r in rows]

    def forget(self, text: str) -> int:
        """Delete an exact-match memory (correction of a wrong auto-save). Returns rows removed."""
        cur = self._db.execute("DELETE FROM memories WHERE text=?", (text,))
        self._db.commit()
        return cur.rowcount

    def forget_like(self, pattern: str) -> int:
        """Delete memories matching a SQL LIKE pattern (e.g. '%name%'). Returns rows removed."""
        cur = self._db.execute("DELETE FROM memories WHERE text LIKE ?", (pattern,))
        self._db.commit()
        return cur.rowcount

    def touch_usage(self, narsese: str, use_count: int, last_used: float) -> None:
        """Snapshot reconciliation of usage/recency (use_count mirrors ONA's usage signal)."""
        self._db.execute("UPDATE facts SET use_count=?, last_used=? WHERE narsese=?",
                         (use_count, last_used, narsese))
        self._db.commit()

    def pin(self, narsese: str, priority_tier: int = 1) -> None:
        self._db.execute("UPDATE facts SET pinned=1, priority_tier=? WHERE narsese=?",
                         (priority_tier, narsese))
        self._db.commit()

    def unpin(self, narsese: str) -> None:
        self._db.execute("UPDATE facts SET pinned=0 WHERE narsese=?", (narsese,))
        self._db.commit()

    def get(self, narsese: str) -> Fact | None:
        row = self._db.execute(f"SELECT {_COLS} FROM facts WHERE narsese=?", (narsese,)).fetchone()
        return _row_to_fact(row) if row else None

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def facts_for_reload(self, limit: int = 40) -> list[Fact]:
        """Cache-miss repopulation order: pinned first, then most protected/recent."""
        rows = self._db.execute(
            f"SELECT {_COLS} FROM facts ORDER BY pinned DESC, priority_tier DESC, last_used DESC LIMIT ?",
            (limit,)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def prune(self, max_rows: int) -> int:
        """Evict least-useful UNPINNED rows when over capacity. Pinned rows are immune."""
        over = self.count() - max_rows
        if over <= 0:
            return 0
        cur = self._db.execute(
            """DELETE FROM facts WHERE id IN (
                 SELECT id FROM facts WHERE pinned=0
                 ORDER BY priority_tier ASC, use_count ASC, last_used ASC LIMIT ?)""", (over,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        self._db.close()
