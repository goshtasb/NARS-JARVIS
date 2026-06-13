"""Durable summary archive (ADR-058) — Imperative Shell (S-02). One cohesive table on the shared
`jarvis.db`: the text of every *briefed* document summary (the Canvas/overnight `summarize_file` path).

Why text, in the daemon: an overnight summary finishes even while the macOS app is closed, so the
archive's source of truth cannot live in the UI. The Swift client materializes each record into an
openable PDF (`~/Documents/JARVIS Summaries/`) — keeping PDF rendering native and adding no Python
dependency. Mirrors `overnight/store.py`: schema constant, shared `db_path`, `CREATE TABLE IF NOT
EXISTS` is the whole migration story for v1.
"""
from __future__ import annotations

import time

import dbconn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,                      -- basename shown in the Summary tab
  source_path TEXT NOT NULL DEFAULT '',           -- the document that was summarized
  text        TEXT NOT NULL,                      -- the summary body (the durable archive)
  created_at  REAL NOT NULL
);
"""


def _now(now: float | None) -> float:
    return time.time() if now is None else now


class SummaryArchive:
    """The persistent list of briefed document summaries. Append-only; the UI lists and reads them."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def add(self, source_name: str, source_path: str, text: str, now: float | None = None) -> int:
        cur = self._db.execute(
            "INSERT INTO summaries(source_name,source_path,text,created_at) VALUES(?,?,?,?)",
            (source_name, source_path, text, _now(now)))
        self._db.commit()
        return cur.lastrowid

    def has(self, source_path: str, text: str) -> bool:
        """Already archived? Used by the one-time backfill to stay idempotent (no duplicate rows)."""
        return self._db.execute(
            "SELECT 1 FROM summaries WHERE source_path=? AND text=? LIMIT 1",
            (source_path, text)).fetchone() is not None

    def list(self) -> list[dict]:
        """Newest first. Body omitted — the tab list needs only name, date, and size."""
        rows = self._db.execute(
            "SELECT id,source_name,created_at,LENGTH(text) FROM summaries ORDER BY id DESC").fetchall()
        return [dict(zip(("id", "source_name", "created_at", "chars"), r)) for r in rows]

    def get(self, sid: int) -> dict | None:
        row = self._db.execute(
            "SELECT id,source_name,source_path,text,created_at FROM summaries WHERE id=?",
            (sid,)).fetchone()
        return dict(zip(("id", "source_name", "source_path", "text", "created_at"), row)) if row else None

    def close(self) -> None:
        self._db.close()
