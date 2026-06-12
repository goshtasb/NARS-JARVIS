"""Local ingestion telemetry (L2) — measures GATE FRICTION, not the user's thoughts.

Privacy-absolute: this table stores only outcome CATEGORIES (timestamp, session, layer, outcome).
It NEVER stores the raw English input or the compiled Narsese — we measure whether the user is
learning the constrained dialect (healthy rejection-rate decay), not what they said. No cloud, no
phone-home; a single local SQLite table.
"""
from __future__ import annotations

import sqlite3

import dbconn
import time
from collections import Counter

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  REAL NOT NULL,
    session_id TEXT NOT NULL,
    layer      TEXT,            -- 'L0' | 'L1' | NULL
    outcome    TEXT NOT NULL    -- see OUTCOMES below
);
"""

# The only values `outcome` may take. No free text ever reaches this table.
OUTCOMES = (
    "COMMIT_CLEAN",        # gate committed (layer says L0 fast-path or L1 semantic)
    "REJECT_STRUCTURAL",   # L0: non-taxonomic verb / non-whitelisted shape
    "REJECT_FUSED",        # L0: fused multi-concept atom
    "REJECT_SEMANTIC",     # L1: cosine below reject threshold
    "ESCALATE_ACCEPTED",   # L1 ambiguous -> human said yes
    "ESCALATE_DECLINED",   # L1 ambiguous -> human said no
)
_REJECTIONS = frozenset({"REJECT_STRUCTURAL", "REJECT_FUSED", "REJECT_SEMANTIC", "ESCALATE_DECLINED"})


class MetricsStore:
    """Append-only ingestion telemetry + the rejection-rate-decay readout. Fire-and-forget writes."""

    def __init__(self, db_path: str = ":memory:", session_id: str = "session") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._session = session_id

    def record_batch(self, outcomes: list[tuple[str | None, str]]) -> None:
        """Persist one (layer, outcome) per evaluated claim. Never raises — telemetry must never
        break ingestion, and one batched commit keeps it off the perceived-latency path."""
        if not outcomes:
            return
        try:
            now = time.time()
            self._db.executemany(
                "INSERT INTO ingestion_metrics(timestamp, session_id, layer, outcome) VALUES (?,?,?,?)",
                [(now, self._session, layer, outcome) for (layer, outcome) in outcomes],
            )
            self._db.commit()
        except Exception:  # noqa: BLE001 — fire-and-forget
            pass

    @staticmethod
    def _rate(rows: list[tuple[str, str]]) -> float | None:
        return (sum(1 for _, o in rows if o in _REJECTIONS) / len(rows)) if rows else None

    def summary(self) -> dict:
        """Rejection-rate decay: current session vs prior sessions, plus the failure taxonomy."""
        rows = self._db.execute("SELECT session_id, outcome FROM ingestion_metrics").fetchall()
        session = [r for r in rows if r[0] == self._session]
        prior = [r for r in rows if r[0] != self._session]
        return {
            "total": len(rows),
            "global_rate": self._rate(rows),
            "session_total": len(session), "session_rate": self._rate(session),
            "prior_total": len(prior), "prior_rate": self._rate(prior),
            "taxonomy": dict(Counter(o for _, o in rows)),
        }

    def close(self) -> None:
        self._db.close()
