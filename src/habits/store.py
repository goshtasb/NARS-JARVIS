"""Durable mirror of the habit beliefs (ADR-026 / ADR-028). Imperative Shell (S-02). Mirrors
`sentinel.store`.

ONA has no save/load, so each habit's current truth (frequency/confidence) is written through here and
replayed into a fresh brain on daemon start (ADR-011 pattern). Records the (bucket, action, arg) plus
the Phase-2 context (`day_type`, `app`) and a `scope` ('base' temporal tendency vs 'context' full habit)
so the proposal tick can enumerate candidates for the current context.
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
  updated_at    REAL NOT NULL,
  day_type      TEXT NOT NULL DEFAULT '',
  app           TEXT NOT NULL DEFAULT '',
  scope         TEXT NOT NULL DEFAULT 'base'
);
"""
# ADR-028 columns added after the ADR-026 table shipped — migrate older DBs in place.
_PHASE2_COLUMNS = (("day_type", "''"), ("app", "''"), ("scope", "'base'"))


class HabitStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        have = {row[1] for row in self._db.execute("PRAGMA table_info(habits)").fetchall()}
        for col, default in _PHASE2_COLUMNS:
            if col not in have:
                self._db.execute(f"ALTER TABLE habits ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")

    def record(self, key: str, bucket: str, action: str, arg: str, frequency: float, confidence: float,
               now: float | None = None, day_type: str = "", app: str = "", scope: str = "base") -> None:
        """Upsert a habit grain's current truth (called after each evidence injection)."""
        self._db.execute(
            "INSERT INTO habits(key,bucket,action,arg,frequency,confidence,updated_at,day_type,app,scope) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET frequency=excluded.frequency, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (key, bucket, action, arg, frequency, confidence,
             now if now is not None else time.time(), day_type, app, scope))
        self._db.commit()

    def _rows(self, where: str, params: tuple) -> list[dict]:
        cols = "key,bucket,action,arg,frequency,confidence,last_proposed,day_type,app,scope"
        rows = self._db.execute(f"SELECT {cols} FROM habits {where}", params).fetchall()
        keys = cols.split(",")
        return [dict(zip(keys, r)) for r in rows]

    def for_bucket(self, bucket: str) -> list[dict]:
        """All habits recorded for a context bucket (both scopes)."""
        return self._rows("WHERE bucket=?", (bucket,))

    def for_context(self, bucket: str, day_type: str, app: str) -> list[dict]:
        """Context habits matching the CURRENT (bucket, day_type, app) — the proposal candidates (ADR-028)."""
        return self._rows("WHERE bucket=? AND day_type=? AND app=? AND scope='context'",
                          (bucket, day_type, app))

    def list_all(self) -> list[dict]:
        """Full rows for every tracked habit (ADR-027 introspection)."""
        return self._rows("ORDER BY scope DESC, bucket", ())

    def all(self) -> list[tuple[str, float, float]]:
        """(term-key, freq, conf) for every habit — replayed into ONA on start."""
        return [(k, f, c) for (k, f, c) in
                self._db.execute("SELECT key,frequency,confidence FROM habits").fetchall()]

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM habits WHERE key=?", (key,))
        self._db.commit()

    def mark_proposed(self, key: str, day_bucket: str) -> None:
        self._db.execute("UPDATE habits SET last_proposed=? WHERE key=?", (day_bucket, key))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
