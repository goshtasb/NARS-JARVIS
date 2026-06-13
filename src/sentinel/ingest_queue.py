"""Passive-ingestion candidate queue (v1.24.0) — the durable landing spot between CAPTURE and INGEST.

The FSEvents edge captures *candidate file paths* (cheap, event-driven); this is where they wait, on
disk, until the overnight runner drains and actually ingests them (the expensive LLM/embedding work). One
row per path (UNIQUE), so a file saved 50× collapses to one pending candidate. `status` carries the
micro-ingest budget verdict: 'pending' = eligible now, 'deferred' = heavy payload held for AC power.
"""
from __future__ import annotations

import time

import dbconn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL UNIQUE,   -- absolute file path; UNIQUE dedupes re-captures
    bytes       INTEGER NOT NULL,
    captured_at REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'   -- pending | deferred (held for AC) | done
);
"""


class IngestQueue:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def enqueue(self, path: str, num_bytes: int, *, status: str = "pending", now: float | None = None) -> None:
        """Land (or refresh) one candidate. Never raises — capture telemetry must not break the loop."""
        try:
            self._db.execute(
                "INSERT INTO ingest_queue(path, bytes, captured_at, status) VALUES (?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET bytes=excluded.bytes, captured_at=excluded.captured_at, "
                "status=excluded.status",
                (path, int(num_bytes), time.time() if now is None else now, status))
            self._db.commit()
        except Exception:  # noqa: BLE001 — fire-and-forget
            pass

    def pending(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,path,bytes,captured_at,status FROM ingest_queue WHERE status='pending' ORDER BY id"
        ).fetchall()
        return [dict(zip(("id", "path", "bytes", "captured_at", "status"), r)) for r in rows]

    def all(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,path,bytes,captured_at,status FROM ingest_queue ORDER BY id").fetchall()
        return [dict(zip(("id", "path", "bytes", "captured_at", "status"), r)) for r in rows]

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM ingest_queue").fetchone()[0]

    def close(self) -> None:
        self._db.close()
