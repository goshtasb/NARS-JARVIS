"""Durable persona state (ADR-036) — Imperative Shell (S-02). Two tables on the shared `jarvis.db`:

- `persona_concepts`  — the checkpoint: the restorable Narsese tuple `(term, frequency, confidence)`
  per learned persona term. Doubles as the FAST injection source (a SQLite SELECT, no ONA on the hot
  path) and the replay source after a NAR restart. Priority/durability are deliberately NOT stored —
  ONA can't restore them (it recomputes), so they'd be informational-only.
- `persona_events_pending` — the O(1) ingestion buffer: raw events awaiting the idle-gated 7B batch.

Mirrors `habits/store.py` (schema constant, shared db_path; new tables, so CREATE IF NOT EXISTS is the
whole migration story).
"""
from __future__ import annotations

import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_concepts (
  term        TEXT PRIMARY KEY,
  frequency   REAL NOT NULL,
  confidence  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS persona_events_pending (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_text    TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'event',
  created_at  REAL NOT NULL
);
"""


def _now(now: float | None) -> float:
    return time.time() if now is None else now


class PersonaStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # ── ingestion buffer (O(1) append; idle-gated drain) ──
    def buffer_event(self, raw_text: str, kind: str = "event", now: float | None = None) -> None:
        text = (raw_text or "").strip()
        if not text:
            return
        self._db.execute("INSERT INTO persona_events_pending(raw_text,kind,created_at) VALUES(?,?,?)",
                         (text[:2000], kind, _now(now)))
        self._db.commit()

    def pending_batch(self, limit: int) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,raw_text,kind FROM persona_events_pending ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "raw_text": r[1], "kind": r[2]} for r in rows]

    def consume(self, ids: list[int]) -> None:
        if not ids:
            return
        self._db.execute(f"DELETE FROM persona_events_pending WHERE id IN ({','.join('?' * len(ids))})",
                         tuple(ids))
        self._db.commit()

    def pending_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM persona_events_pending").fetchone()[0]

    # ── concept checkpoint (write-through + injection source + replay source) ──
    def upsert_concept(self, term: str, frequency: float, confidence: float, now: float | None = None) -> None:
        self._db.execute(
            "INSERT INTO persona_concepts(term,frequency,confidence,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET frequency=excluded.frequency, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (term, frequency, confidence, _now(now)))
        self._db.commit()

    def current(self, min_confidence: float = 0.75) -> list[dict]:
        """Persona terms confident enough to inject — the hot-path read (no ONA round-trip)."""
        rows = self._db.execute(
            "SELECT term,frequency,confidence FROM persona_concepts WHERE confidence >= ? "
            "ORDER BY confidence DESC", (min_confidence,)).fetchall()
        return [{"term": r[0], "frequency": r[1], "confidence": r[2]} for r in rows]

    def all_concepts(self) -> list[dict]:
        """Every checkpointed concept — re-fed into a fresh ONA on boot / after a restart (ADR-011)."""
        rows = self._db.execute("SELECT term,frequency,confidence FROM persona_concepts").fetchall()
        return [{"term": r[0], "frequency": r[1], "confidence": r[2]} for r in rows]

    def prune(self, min_confidence: float = 0.10) -> int:
        """Drop washed-out concepts so the table stays bounded. Returns rows cleared."""
        cur = self._db.execute("DELETE FROM persona_concepts WHERE confidence < ?", (min_confidence,))
        self._db.commit()
        return cur.rowcount

    def delete(self, term: str) -> int:
        """Forget one learned constraint (ADR-037). Returns rows removed (0 if it wasn't there)."""
        cur = self._db.execute("DELETE FROM persona_concepts WHERE term=?", (term,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        self._db.close()
