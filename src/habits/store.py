"""Durable mirror of the habit beliefs (ADR-026). Imperative Shell (S-02). Mirrors `sentinel.store`.

ONA has no save/load, so each habit's current truth (frequency/confidence) is written through here and
replayed into a fresh brain on daemon start (ADR-011 pattern). Also records the (bucket, action, arg)
so the proposal tick can enumerate candidate habits for the current context.
"""
from __future__ import annotations

import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
  key           TEXT PRIMARY KEY,
  bucket        TEXT NOT NULL,
  action        TEXT NOT NULL,
  arg           TEXT NOT NULL DEFAULT '',
  frequency     REAL NOT NULL,
  confidence    REAL NOT NULL,
  last_proposed TEXT NOT NULL DEFAULT '',
  updated_at    REAL NOT NULL
);
"""


class HabitStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def record(self, key: str, bucket: str, action: str, arg: str,
               frequency: float, confidence: float, now: float | None = None) -> None:
        """Upsert a habit's current truth (called after each evidence injection)."""
        self._db.execute(
            "INSERT INTO habits(key,bucket,action,arg,frequency,confidence,updated_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET frequency=excluded.frequency, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (key, bucket, action, arg, frequency, confidence, now if now is not None else time.time()))
        self._db.commit()

    def for_bucket(self, bucket: str) -> list[dict]:
        """Candidate habits recorded for a context bucket (for the proposal tick)."""
        rows = self._db.execute(
            "SELECT key,action,arg,frequency,confidence,last_proposed FROM habits WHERE bucket=?",
            (bucket,)).fetchall()
        return [{"key": k, "action": a, "arg": g, "frequency": f, "confidence": c, "last_proposed": lp}
                for (k, a, g, f, c, lp) in rows]

    def all(self) -> list[tuple[str, float, float]]:
        """(term-key, freq, conf) for every habit — replayed into ONA on start."""
        return [(k, f, c) for (k, f, c) in
                self._db.execute("SELECT key,frequency,confidence FROM habits").fetchall()]

    def list_all(self) -> list[dict]:
        """Full rows for every tracked habit (ADR-027 introspection)."""
        rows = self._db.execute(
            "SELECT key,bucket,action,arg,frequency,confidence FROM habits ORDER BY bucket").fetchall()
        return [{"key": k, "bucket": b, "action": a, "arg": g, "frequency": f, "confidence": c}
                for (k, b, a, g, f, c) in rows]

    def delete(self, key: str) -> None:
        """Purge a habit (ADR-027 pruning)."""
        self._db.execute("DELETE FROM habits WHERE key=?", (key,))
        self._db.commit()

    def mark_proposed(self, key: str, day_bucket: str) -> None:
        """Record that this habit was proposed for this day-bucket (cooldown: at most once per occurrence)."""
        self._db.execute("UPDATE habits SET last_proposed=? WHERE key=?", (day_bucket, key))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
