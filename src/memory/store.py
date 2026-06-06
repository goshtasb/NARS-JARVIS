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
