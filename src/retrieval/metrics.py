"""Local retrieval telemetry (L2) — proves the vault COMPOUNDS, content-free (ADR-056 §8).

Privacy-absolute, same posture as memory/metrics.py: this table stores no query text and no Narsese —
only a SALTED hash of the resolved anchor/target set, a grounded flag, and (when grounded) the age of the
oldest cited belief. It is local-only; any future aggregate sharing is opt-in and the per-install salt
makes a hash un-bruteforceable and un-correlatable across installs.

Three metrics, all derived at read time from one append-only table:
- FA-LGR — First-Ask Local Grounding Rate: over the FIRST time each distinct topic was asked, the fraction
  the vault grounded (vs abstained). Measures genuine compounding, not caching.
- Stamp-Age Depth — median age (days) of the oldest premise cited in grounded answers. Rising = the vault
  is reasoning from OLD knowledge, not acting as a short-term cache.
- Flywheel Close Rate — of topics that once abstained, the fraction that later grounded locally within 7
  days (the abstain -> Cloud -> alias-harvest -> local-resolution loop, closing).
"""
from __future__ import annotations

import hashlib
import secrets
import time

import dbconn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_metrics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      REAL    NOT NULL,
    topic_hash     TEXT    NOT NULL,   -- salted hash of sorted(resolved anchors ∪ targets); NO text
    grounded       INTEGER NOT NULL,   -- 1 = Tier-1 vault grounded, 0 = abstained
    stamp_age_days REAL                -- oldest-cited-premise age (grounded only), else NULL
);
CREATE TABLE IF NOT EXISTS recall_meta (key TEXT PRIMARY KEY, value TEXT);
"""

_CLOSE_WINDOW_S = 7 * 86400.0     # flywheel: an abstain must re-resolve locally within 7 days to count


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class RecallMetrics:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._salt = self._load_or_make_salt()
        self._db.commit()

    def _load_or_make_salt(self) -> bytes:
        """Per-install irreversibility salt: load it, or mint one on first run. If it's ever lost, a fresh
        salt is minted — old hashes simply stop matching (orphaned), never a crash (graceful)."""
        row = self._db.execute("SELECT value FROM recall_meta WHERE key='salt'").fetchone()
        if row and row[0]:
            try:
                return bytes.fromhex(row[0])
            except ValueError:
                pass
        salt = secrets.token_bytes(16)
        self._db.execute("INSERT OR REPLACE INTO recall_meta(key, value) VALUES ('salt', ?)", (salt.hex(),))
        return salt

    def topic_hash(self, atoms: list[str]) -> str:
        """Salted hash of the sorted, de-duped canonical atom set. The lexicon's deterministic resolution
        (done upstream) is what makes paraphrases collide here; the salt makes the low-entropy atom set
        un-bruteforceable. Empty -> '' (the caller excludes zero-anchor queries)."""
        key = "|".join(sorted({a.strip() for a in atoms if a and a.strip()}))
        if not key:
            return ""
        return hashlib.sha256(self._salt + key.encode()).hexdigest()

    def record(self, topic_hash: str, grounded: bool, *, stamp_age_days: float | None = None,
               now: float | None = None) -> None:
        """Append one recall outcome. Never raises — telemetry must never break the reasoning path."""
        if not topic_hash:
            return                                            # zero-anchor query: excluded for metric purity
        try:
            self._db.execute(
                "INSERT INTO recall_metrics(timestamp, topic_hash, grounded, stamp_age_days) VALUES (?,?,?,?)",
                (time.time() if now is None else now, topic_hash, 1 if grounded else 0, stamp_age_days))
            self._db.commit()
        except Exception:  # noqa: BLE001 — fire-and-forget
            pass

    def summary(self) -> dict:
        rows = self._db.execute(
            "SELECT timestamp, topic_hash, grounded, stamp_age_days FROM recall_metrics ORDER BY timestamp"
        ).fetchall()
        if not rows:
            return {"queries": 0, "topics": 0, "fa_lgr": None,
                    "stamp_age_median_days": None, "flywheel_close_rate": None}
        by_hash: dict[str, list[tuple[float, bool, float | None]]] = {}
        for ts, h, g, age in rows:
            by_hash.setdefault(h, []).append((ts, bool(g), age))

        first_grounded = [sorted(evs)[0][1] for evs in by_hash.values()]   # FA-LGR over each topic's FIRST ask
        ages = [age for _ts, _h, g, age in rows if g and age is not None]
        opened = closed = 0
        for evs in by_hash.values():
            evs = sorted(evs)
            first_abstain = next((ts for ts, g, _ in evs if not g), None)
            if first_abstain is None:
                continue
            opened += 1
            if any(g and first_abstain <= ts <= first_abstain + _CLOSE_WINDOW_S for ts, g, _ in evs):
                closed += 1
        return {
            "queries": len(rows),
            "topics": len(by_hash),
            "fa_lgr": sum(first_grounded) / len(first_grounded),
            "stamp_age_median_days": _median(ages),
            "flywheel_close_rate": (closed / opened) if opened else None,
        }

    def trend(self, now: float | None = None) -> dict:
        """Period-over-period FA-LGR for the 'compounding trajectory' headline: the grounding rate over
        topics FIRST asked in the current 30 days vs the prior 30. Each window also carries its N so the UI
        can gate on data-sufficiency (a 2-topic month is noise, not a trend). Content-free, read-time."""
        now = time.time() if now is None else now
        rows = self._db.execute(
            "SELECT timestamp, topic_hash, grounded FROM recall_metrics ORDER BY timestamp").fetchall()
        by_hash: dict[str, tuple[float, bool]] = {}
        for ts, h, g in rows:
            if h not in by_hash:                              # rows are time-ordered -> first seen = first ask
                by_hash[h] = (ts, bool(g))
        firsts = list(by_hash.values())
        day = 86400.0

        def window(lo: float, hi: float) -> tuple[float | None, int]:
            sel = [g for ts, g in firsts if lo <= ts < hi]
            return ((sum(sel) / len(sel)) if sel else None, len(sel))

        cur_rate, cur_n = window(now - 30 * day, now + 1)
        prior_rate, prior_n = window(now - 60 * day, now - 30 * day)
        return {"current_fa_lgr": cur_rate, "current_n": cur_n,
                "prior_fa_lgr": prior_rate, "prior_n": prior_n}

    def close(self) -> None:
        self._db.close()
