"""Passive-ingestion candidate queue (v1.24.0) — the durable barrier between CAPTURE and INGEST.

The FSEvents edge captures candidate paths (cheap, event-driven); they wait here, on disk, until the
overnight drain validates and hands each to the chunker (the expensive LLM work). The table is a
*collapsing barrier* against filesystem churn: one row per path (UNIQUE), so 40 saves -> one candidate.

State machine (Sprint 2):
    pending  ── claim ──►  running  ── valid+changed ──►  done   (+content_hash, +mtime)
    deferred ─ on-AC ─►    running  ── vanished ────────►  gone   (terminal: deleted/out-of-scope)
                          running  ── transient/err ────►  pending (attempts+1, next_attempt_at += backoff)
                                                            └ at CAP ─► gone (OS) / failed (ingest)
`content_hash` + `mtime` are written ONLY on a successful drain (the dedup keys); a re-capture UPSERT
preserves them so the drain can detect "identical content -> skip inference". `next_attempt_at` is the
temporal-backoff gate so a yanked external drive isn't retried in consecutive select() ticks.
"""
from __future__ import annotations

import sys
import time

import dbconn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT    NOT NULL UNIQUE,   -- absolute file path; UNIQUE dedupes re-captures
    bytes           INTEGER NOT NULL,
    captured_at     REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending|deferred|running|done|gone|failed
    content_hash    TEXT,                       -- last successfully-drained content hash (the dedup key)
    mtime           REAL,                       -- last-drained mtime (rsync-style fast path)
    attempts        INTEGER NOT NULL DEFAULT 0, -- transient-error / ingest-failure retry counter
    next_attempt_at REAL    NOT NULL DEFAULT 0  -- temporal backoff: not eligible before this epoch
);
"""

_COLUMNS = {"content_hash": "TEXT", "mtime": "REAL", "attempts": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "REAL NOT NULL DEFAULT 0"}


class IngestQueue:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._migrate()                              # PR#4 created an older 5-column table; add the rest
        self._db.commit()

    def _migrate(self) -> None:
        have = {r[1] for r in self._db.execute("PRAGMA table_info(ingest_queue)")}
        for name, decl in _COLUMNS.items():
            if name not in have:
                self._db.execute(f"ALTER TABLE ingest_queue ADD COLUMN {name} {decl}")

    def enqueue(self, path: str, num_bytes: int, *, status: str = "pending", now: float | None = None) -> None:
        """Land (or refresh) a candidate. A re-capture UPSERTs the SAME row (UNIQUE path) and resets the
        retry budget, but PRESERVES content_hash/mtime so the drain can still skip identical content.
        Never raises — logs for observability, but a per-row hiccup must not break the capture loop."""
        try:
            self._db.execute(
                "INSERT INTO ingest_queue(path, bytes, captured_at, status) VALUES (?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET bytes=excluded.bytes, captured_at=excluded.captured_at, "
                "status=excluded.status, attempts=0, next_attempt_at=0",   # content_hash/mtime preserved
                (path, int(num_bytes), time.time() if now is None else now, status))
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[ingest_queue] enqueue failed for {path}: {exc}\n")

    def reset_running(self) -> int:
        """Crash recovery: any row left 'running' when the daemon died is re-queued (the drain claims at
        most one at a time, so a crash strands at most one). Called once at startup."""
        cur = self._db.execute("UPDATE ingest_queue SET status='pending' WHERE status='running'")
        self._db.commit()
        return cur.rowcount

    def claim_next(self, now: float, on_ac: bool) -> dict | None:
        """Claim the oldest eligible candidate and mark it 'running'. Eligible = pending (or deferred when
        on AC) AND past its backoff gate. Single-threaded daemon -> SELECT-then-UPDATE is race-free."""
        row = self._db.execute(
            "SELECT id,path,bytes,captured_at,status,content_hash,mtime,attempts,next_attempt_at "
            "FROM ingest_queue WHERE (status='pending' OR (status='deferred' AND ?)) AND next_attempt_at<=? "
            "ORDER BY captured_at LIMIT 1", (1 if on_ac else 0, now)).fetchone()
        if row is None:
            return None
        keys = ("id", "path", "bytes", "captured_at", "status", "content_hash", "mtime", "attempts",
                "next_attempt_at")
        d = dict(zip(keys, row))
        self._db.execute("UPDATE ingest_queue SET status='running' WHERE id=?", (d["id"],))
        self._db.commit()
        return d

    def mark_done(self, row_id: int, content_hash: str, mtime: float) -> None:
        self._db.execute("UPDATE ingest_queue SET status='done', content_hash=?, mtime=?, attempts=0 "
                         "WHERE id=?", (content_hash, mtime, row_id))
        self._db.commit()

    def mark_terminal(self, row_id: int, status: str) -> None:
        """A terminal non-success state: 'gone' (vanished/out-of-scope) or 'failed' (ingest gave up)."""
        self._db.execute("UPDATE ingest_queue SET status=? WHERE id=?", (status, row_id))
        self._db.commit()

    def schedule_retry(self, row_id: int, next_attempt_at: float) -> None:
        """Bounded temporal backoff: bump attempts, defer eligibility, return to 'pending'."""
        self._db.execute("UPDATE ingest_queue SET status='pending', attempts=attempts+1, next_attempt_at=? "
                         "WHERE id=?", (next_attempt_at, row_id))
        self._db.commit()

    def pending(self) -> list[dict]:
        return self._select("WHERE status='pending'")

    def all(self) -> list[dict]:
        return self._select("")

    def _select(self, where: str) -> list[dict]:
        rows = self._db.execute(
            f"SELECT id,path,bytes,captured_at,status,content_hash,mtime,attempts,next_attempt_at "
            f"FROM ingest_queue {where} ORDER BY id").fetchall()
        keys = ("id", "path", "bytes", "captured_at", "status", "content_hash", "mtime", "attempts",
                "next_attempt_at")
        return [dict(zip(keys, r)) for r in rows]

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM ingest_queue").fetchone()[0]

    def close(self) -> None:
        self._db.close()
