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
-- ADR-011: durable ONA belief truths so earned autonomy + the steadiness baseline survive a restart.
-- ONA has no save/load, so we persist truths and REPLAY them into a fresh sentinel brain on start
-- (mirrors the knowledge brain's memory.reload_into_brain). Terms only — no app id, title, or content.
CREATE TABLE IF NOT EXISTS sentinel_beliefs (
    term       TEXT PRIMARY KEY,   -- e.g. "<distracted_hide_comms --> [approved]>" or a steadiness term
    frequency  REAL NOT NULL,
    confidence REAL NOT NULL,
    updated_at REAL NOT NULL
);
-- ADR-048: remember whether the user wants the Flow Sentinel on, so it AUTO-STARTS at the next daemon
-- boot instead of needing a manual `sentinel on` every restart (the silent-no-learning bug).
CREATE TABLE IF NOT EXISTS sentinel_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- ADR-050 (passive-observation slice): a durable log of what the user actually does — one row per
-- foreground app switch the sentinel observes. CONTENT-BLIND by design: bundle id + coarse category +
-- timestamp ONLY — never a window title, url, or document. This is the raw stream the Cognitive
-- Identity's "What I've noticed" view aggregates; it is NOT fed to the action firewall.
CREATE TABLE IF NOT EXISTS usage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle     TEXT NOT NULL,
    bucket     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_events(created_at);
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
        self._last_usage_prune = 0.0   # ADR-050: drives the deterministic (boot + hourly) usage prune

    def enabled(self) -> bool:
        """Whether the Flow Sentinel should auto-start at daemon boot (ADR-048). Defaults to **on**
        when the user has never set a preference — observing habits is the assistant's core job, so it
        should learn by default; a deliberate `sentinel off` is persisted and survives restarts."""
        row = self._db.execute("SELECT value FROM sentinel_settings WHERE key='enabled'").fetchone()
        return row is None or row[0] == "1"

    # ── passive-usage log (ADR-050 slice) ──
    def prune_usage(self, now: float, retain_days: float = 30.0) -> int:
        """Delete usage rows older than the retention window; returns rows removed. Deterministic —
        the bounded-disk guarantee, not the old probabilistic `%50` heuristic."""
        cur = self._db.execute("DELETE FROM usage_events WHERE created_at < ?",
                              (now - retain_days * 86400,))
        self._db.commit()
        self._last_usage_prune = now
        return cur.rowcount

    def record_usage(self, bundle: str, bucket: str, now: float, *, retain_days: float = 30.0) -> None:
        """Append one foreground-app-switch observation (content-blind). The 30-day window is enforced
        deterministically: prune on the FIRST write after boot (`_last_usage_prune == 0`) and at most
        hourly thereafter — bounded frequency, not coincidence-of-timestamp."""
        self._db.execute("INSERT INTO usage_events(bundle, bucket, created_at) VALUES (?,?,?)",
                         (bundle, bucket, now))
        self._db.commit()
        if now - self._last_usage_prune >= 3600.0:      # boot (last=0) + hourly — deterministic
            self.prune_usage(now, retain_days)

    def recent_usage(self, since: float) -> list[dict]:
        """All usage observations at/after `since` (unix seconds), oldest first. Read-only."""
        rows = self._db.execute(
            "SELECT bundle, bucket, created_at FROM usage_events WHERE created_at >= ? ORDER BY created_at",
            (since,)).fetchall()
        return [{"bundle": b, "bucket": k, "created_at": t} for b, k, t in rows]

    def set_enabled(self, on: bool) -> None:
        """Persist the user's on/off choice so it survives a restart (ADR-048)."""
        self._db.execute("INSERT OR REPLACE INTO sentinel_settings(key, value) VALUES ('enabled', ?)",
                         ("1" if on else "0",))
        self._db.commit()

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

    # ── ADR-011: durable belief truths (gate authorizations + steadiness baseline) ──
    def record_belief(self, term: str, frequency: float, confidence: float,
                      now: float | None = None) -> None:
        """Write-through one ONA belief truth; latest value wins. Called on the discrete, low-frequency
        events (a consent decision, a Schmitt-trigger baseline shift), so it never thrashes the disk."""
        now = time.time() if now is None else now
        self._db.execute(
            "INSERT INTO sentinel_beliefs(term, frequency, confidence, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET frequency=excluded.frequency, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (term, frequency, confidence, now))
        self._db.commit()

    def beliefs(self) -> list[tuple[str, float, float]]:
        """All persisted (term, frequency, confidence) — replayed into a fresh sentinel brain on start."""
        return [(t, f, c) for t, f, c in
                self._db.execute("SELECT term, frequency, confidence FROM sentinel_beliefs").fetchall()]

    def close(self) -> None:
        self._db.close()
