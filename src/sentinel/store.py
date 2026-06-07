"""Sentinel-domain persistence (L2) — the app->bucket categorization memoizer.

Maps (bundle_id, LSApplicationCategoryType) to our internal taxonomy ONCE and caches it, so the
classify work (and any user override) happens a single time per novel app and survives restarts —
the same memoization discipline as grounding. Stores categories only; never window titles/contents.
"""
from __future__ import annotations

import sqlite3
import time

from .focusblock import Block, lift
from .sensor import classify

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_categories (
    bundle_id   TEXT PRIMARY KEY,
    bucket      TEXT NOT NULL,
    ls_category TEXT,
    source      TEXT NOT NULL,     -- 'auto' | 'override'
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS focus_blocks (
    start    REAL NOT NULL,        -- wall-clock start (low-cardinality: a few/day; no app/content)
    duration REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interventions (
    ts       REAL NOT NULL,        -- when offered
    accepted INTEGER NOT NULL      -- 1=user said yes (apps hidden), 0=declined
);
CREATE TABLE IF NOT EXISTS calibration (
    crossed_at   REAL NOT NULL,    -- the ONE moment the steady baseline first reached the floor
    elapsed_s    REAL NOT NULL,    -- seconds of sentinel-on time to get there  (= empirical burn-in)
    observations INTEGER NOT NULL  -- how many steadiness observations it took (numeric only, no content)
);
"""


class SentinelStore:
    """Cache of bundle -> bucket. resolve() memoizes classify(); set_override() pins a manual bucket."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._cache: dict[str, str] = dict(
            self._db.execute("SELECT bundle_id, bucket FROM app_categories").fetchall()
        )

    def resolve(self, bundle_id: str, ls_category: str = "") -> str:
        """Return the cached bucket, or classify once (override -> UTI -> other), cache, and return."""
        if bundle_id in self._cache:
            return self._cache[bundle_id]
        bucket = classify(bundle_id, ls_category)
        self._db.execute(
            "INSERT OR IGNORE INTO app_categories(bundle_id, bucket, ls_category, source, created_at) "
            "VALUES (?,?,?,?,?)",
            (bundle_id, bucket, ls_category, "auto", time.time()),
        )
        self._db.commit()
        self._cache[bundle_id] = bucket
        return bucket

    def set_override(self, bundle_id: str, bucket: str) -> None:
        """User pins a manual bucket (for the rare app Apple mislabels or omits)."""
        self._db.execute(
            "INSERT INTO app_categories(bundle_id, bucket, ls_category, source, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(bundle_id) DO UPDATE SET bucket=excluded.bucket, source='override'",
            (bundle_id, bucket, "", "override", time.time()),
        )
        self._db.commit()
        self._cache[bundle_id] = bucket

    def uncategorized(self) -> list[str]:
        """Bundles that fell through to 'other' — surfaced so the user could one-time-override them."""
        return [b for b, k in self._cache.items() if k == "other"]

    # ── Value KPI: focus blocks + intervention lift (timestamps are wall-clock, caller-injected) ──
    def record_focus_block(self, start: float, duration: float) -> None:
        self._db.execute("INSERT INTO focus_blocks(start, duration) VALUES (?,?)", (start, duration))
        self._db.commit()

    def record_intervention(self, ts: float, accepted: bool) -> None:
        self._db.execute("INSERT INTO interventions(ts, accepted) VALUES (?,?)",
                         (ts, 1 if accepted else 0))
        self._db.commit()

    def kpi(self) -> dict:
        """Intervention-lift readout: focus-block duration after vs before accepted nudges."""
        blocks = [Block(s, d) for s, d in
                  self._db.execute("SELECT start, duration FROM focus_blocks").fetchall()]
        interventions = [(ts, bool(a)) for ts, a in
                         self._db.execute("SELECT ts, accepted FROM interventions").fetchall()]
        return lift(blocks, interventions)

    def record_burnin(self, crossed_at: float, elapsed_s: float, observations: int) -> None:
        """Record the ONE moment the baseline crossed the floor — only if not already recorded."""
        if self._db.execute("SELECT 1 FROM calibration LIMIT 1").fetchone() is None:
            self._db.execute(
                "INSERT INTO calibration(crossed_at, elapsed_s, observations) VALUES (?,?,?)",
                (crossed_at, elapsed_s, observations))
            self._db.commit()

    def calib(self) -> dict:
        """Local, numeric-only calibration readout: empirical burn-in + false-positive proxy.
        Everything here is a scalar — the only thing a human ever needs to relay to tune the floor.
        No app, no category, no title ever enters this path."""
        row = self._db.execute(
            "SELECT elapsed_s, observations FROM calibration LIMIT 1").fetchone()
        fired = self._db.execute("SELECT COUNT(*) FROM interventions").fetchone()[0]
        declined = self._db.execute(
            "SELECT COUNT(*) FROM interventions WHERE accepted=0").fetchone()[0]
        return {
            "burnin_elapsed_s": row[0] if row else None,      # empirical burn-in duration
            "burnin_observations": row[1] if row else None,   # ...in steadiness observations
            "fired": fired,
            "declined": declined,
            # Decline rate is our false-positive PROXY: a high rate => floor too low, raise it.
            "decline_rate": (declined / fired) if fired else None,
        }

    def close(self) -> None:
        self._db.close()
