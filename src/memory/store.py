"""SQLite system-of-record (L2). Imperative Shell (S-02) — durable, pinnable.

ONA (L1) self-forgets to protect its 40-slot buffer; this store is the permanent safety net.
Pinned facts are immune to pruning. See PRD §6 and the approved sync model (write-through /
observe / snapshot / cache-miss reload). There is no eviction callback (ONA evicts silently);
durability comes from write-through at ingestion + reconciliation.
"""
from __future__ import annotations

import math
import sqlite3

import dbconn
import time

from .fact import Fact, pack_embedding, unpack_embedding
from .slots import same_single_valued_slot, slot_of

# Cosine pre-filter for conflict detection (ADR-009): deliberately PERMISSIVE — the slot check is the
# authoritative gate, cosine only prunes clearly-unrelated rows for scale (a same-slot pair always
# scores well above this). Retrieval ranking uses cosine directly, no floor.
_CONFLICT_CANDIDATE_COSINE = 0.5


def _norm_text(s: str) -> str:
    """Loose canonical form for forget matching (case / whitespace / surrounding punctuation)."""
    import re
    return re.sub(r"\s+", " ", s.strip().lower()).strip(" .,!?;:\"'")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  narsese       TEXT    NOT NULL UNIQUE,
  english       TEXT,
  source        TEXT,                       -- v1.24.0: provenance tier (NULL/told/cloud/passive); NULL = legacy/trusted
  frequency     REAL    NOT NULL,
  confidence    REAL    NOT NULL,
  embedding     BLOB,
  pinned        INTEGER NOT NULL DEFAULT 0,
  priority_tier INTEGER NOT NULL DEFAULT 0,
  use_count     INTEGER NOT NULL DEFAULT 1,
  active        INTEGER NOT NULL DEFAULT 1,   -- v1.24.0 Step 3: 0 = tombstoned by the passive decay sweep
  created_at    REAL    NOT NULL,
  updated_at    REAL    NOT NULL,
  last_used     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_pin ON facts(pinned, priority_tier);

-- Conversational memory (ADR-008): free-form English facts auto-extracted from chat. Kept
-- SEPARATE from `facts` so it carries no Narsese mirror constraint — anything the LLM decides
-- to remember (preferences, tasks, self-facts) is durable here even when it has no clean ONA
-- form. `_recall` merges this with `facts.english`. `text` is unique so identical saves dedup.
CREATE TABLE IF NOT EXISTS memories (
  id            INTEGER PRIMARY KEY,
  text          TEXT    NOT NULL,
  source        TEXT,
  embedding     BLOB,
  pinned        INTEGER NOT NULL DEFAULT 0,
  use_count     INTEGER NOT NULL DEFAULT 1,
  active        INTEGER NOT NULL DEFAULT 1,   -- 0 = tombstoned (superseded or forgotten); ADR-009
  superseded_by INTEGER,                      -- id of the memory that replaced this one (chain link)
  superseded_at REAL,
  created_at    REAL    NOT NULL,
  updated_at    REAL    NOT NULL,
  last_used     REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_text ON memories(text);
-- NOTE: the idx_memories_active index is created in _migrate(), AFTER the `active` column is added —
-- never here, because on a pre-ADR-009 DB this script runs before `active` exists (it would error).
"""

# Columns added to `memories` after its first release (ADR-009). Applied in-place by `_migrate`
# (SQLite lacks ADD COLUMN IF NOT EXISTS), so an existing jarvis.db upgrades on next open.
_MEMORIES_ADDED_COLUMNS = (
    ("active", "INTEGER NOT NULL DEFAULT 1"),
    ("superseded_by", "INTEGER"),
    ("superseded_at", "REAL"),
)

# v1.24.0 facts columns added after first release. Each is an O(1) metadata-only ADD COLUMN (no table
# rewrite); re-runs are no-ops. `source` (Step 1): provenance tier, NULL = legacy/trusted. `active` (Step 3):
# soft-delete flag for the passive decay sweep — NOT NULL DEFAULT 1, so every existing row upgrades to
# active (a constant default is still O(1) and never rewrites rows).
_FACTS_ADDED_COLUMNS = (
    ("source", "TEXT"),
    ("active", "INTEGER NOT NULL DEFAULT 1"),
)

# ── v1.24.0 Step 3: Value-rank + decay (the L2 tuning surface — change constants HERE, nowhere else) ──
# V(b) = s(b)·(α·c(b) + β·u(b)); pinned beliefs always rank above the unpinned (handled separately).
#   s(b) source authority: a passive observation is low-authority; every other tier (told / NULL legacy /
#         cloud) is trusted (1.0). c(b) = the NAL confidence. u(b) = recency in (0,1], 1/(1+age_days/τ).
# Ratified (Phase 1 floor + Step 3 "Moderate" predicate, Synapse 2026-06-13).
_AUTH_PASSIVE = 0.4          # authority of source='passive'; all trusted tiers = 1.0
_RANK_ALPHA = 0.5            # confidence weight
_RANK_BETA = 0.5            # recency weight
_RECENCY_TAU_DAYS = 15.0     # recency scale: u = 1/(1 + age_days/τ)  (u=1 fresh, ->0 as it ages)
# Decay sweep (Moderate): tombstone a passive belief ONLY when ALL hold — source='passive' AND not pinned
# AND never recalled (use_count<=1, so any corroboration/use spares it) AND unused > AGE days AND V < θ.
_SWEEP_AGE_DAYS = 30.0
_SWEEP_V_THETA = 0.18


def _value_sql(now_param: str = "?") -> str:
    """V(b) as a SQL expression over a `facts` row; `now_param` is the bind placeholder for epoch-seconds
    'now'. Built from module constants only (never user input), so f-string interpolation is safe."""
    age_days = f"(({now_param} - last_used) / 86400.0)"
    recency = f"(1.0 / (1.0 + {age_days} / {_RECENCY_TAU_DAYS}))"
    authority = f"(CASE WHEN source='passive' THEN {_AUTH_PASSIVE} ELSE 1.0 END)"
    return f"({authority} * ({_RANK_ALPHA} * confidence + {_RANK_BETA} * {recency}))"

_COLS = (
    "narsese, english, frequency, confidence, embedding, pinned, "
    "priority_tier, use_count, created_at, updated_at, last_used"
)

# ADR-056/Gate 2: an FTS5 term index over facts.narsese for term-scoped candidate retrieval (deletes the
# recency-capped whole-table scan and its silent forgetting cliff). EXTERNAL-CONTENT (content='facts') so
# it duplicates no text — a purely derived index; the `facts` table stays the single source of truth and
# the index can be dropped/rebuilt with zero data loss. `tokenchars '_'` keeps compound atoms whole
# (`transaction_timeout` is one token, not `transaction`+`timeout`) — without it, FTS would re-introduce
# the exact word-overlap-≠-adjacency bug Stage 2 eliminates. Three triggers keep it in sync across ALL
# fact mutation paths (insert/update/delete) at the DB level — no per-call-site Python sync to drift.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  narsese, content='facts', content_rowid='id', tokenize="unicode61 tokenchars '_'");
CREATE TRIGGER IF NOT EXISTS facts_fts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, narsese) VALUES (new.id, new.narsese);
END;
CREATE TRIGGER IF NOT EXISTS facts_fts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, narsese) VALUES('delete', old.id, old.narsese);
END;
CREATE TRIGGER IF NOT EXISTS facts_fts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, narsese) VALUES('delete', old.id, old.narsese);
  INSERT INTO facts_fts(rowid, narsese) VALUES (new.id, new.narsese);
END;
"""


def _row_to_fact(r: tuple) -> Fact:
    return Fact(r[0], r[1], r[2], r[3], unpack_embedding(r[4]), bool(r[5]),
                r[6], r[7], r[8], r[9], r[10])


class MemoryStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Additive, idempotent schema evolution for `memories` (ADR-009). SQLite has no
        ADD COLUMN IF NOT EXISTS, so add only the columns PRAGMA reports missing — no data loss,
        no crash-loop on restart."""
        have = {row[1] for row in self._db.execute("PRAGMA table_info(memories)")}
        for name, decl in _MEMORIES_ADDED_COLUMNS:
            if name not in have:
                self._db.execute(f"ALTER TABLE memories ADD COLUMN {name} {decl}")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active)")
        # v1.24.0: same additive, idempotent pattern for facts.source (O(1) metadata-only ADD COLUMN;
        # re-runs are no-ops; legacy rows read NULL).
        have_facts = {row[1] for row in self._db.execute("PRAGMA table_info(facts)")}
        for name, decl in _FACTS_ADDED_COLUMNS:
            if name not in have_facts:
                self._db.execute(f"ALTER TABLE facts ADD COLUMN {name} {decl}")
        # Index created AFTER the column exists (a pre-Step-3 DB runs the CREATE script before `active` is
        # added), mirroring idx_memories_active. Speeds the sweep's active=1/source filter on a large L2.
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(active)")
        # ADR-056/Gate 2: the FTS5 term index + sync triggers (idempotent). One-time backfill if the index
        # is empty but facts already exist (a fresh index over a pre-existing DB); thereafter the triggers
        # keep it current, so no rebuild on subsequent opens.
        self._db.executescript(_FTS_SCHEMA)
        # `count(*) FROM facts_fts` reflects the EXTERNAL CONTENT (facts), not the index, so it's never 0
        # while facts exist — the old guard meant the one-time backfill never fired and legacy facts (those
        # inserted before facts_fts existed) were silently never indexed -> recall missed them. The real
        # index-population signal is the FTS5 `_docsize` shadow table (one row per INDEXED document).
        indexed = self._db.execute("SELECT count(*) FROM facts_fts_docsize").fetchone()[0]
        if indexed == 0 and self._db.execute("SELECT count(*) FROM facts").fetchone()[0] > 0:
            self._db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")

    def upsert(self, narsese: str, frequency: float, confidence: float,
               english: str | None = None, embedding: list[float] | None = None,
               now: float | None = None, source: str | None = None) -> None:
        """Write-through (creation) AND observe (revision): insert or update truth in place.

        On conflict, truth is overwritten, english/embedding kept if the new value is None,
        use_count incremented, recency bumped. Pinning is managed separately via pin()/unpin().

        `source` (v1.24.0 provenance) is set ONCE, at row creation, and DELIBERATELY left untouched on
        conflict: a re-observation must never rewrite an existing belief's tier — above all it must never
        downgrade a NULL-trusted (legacy/told) belief to 'passive' just because the firehose saw it again.
        Promotion (passive -> trusted) is the decay/rank engine's job (Step 3), not a write-path side effect.
        """
        now = time.time() if now is None else now
        self._db.execute(
            """INSERT INTO facts (narsese, english, source, frequency, confidence, embedding,
                                  use_count, created_at, updated_at, last_used)
               VALUES (?,?,?,?,?,?,1,?,?,?)
               ON CONFLICT(narsese) DO UPDATE SET
                 frequency=excluded.frequency,
                 confidence=excluded.confidence,
                 english=COALESCE(excluded.english, facts.english),
                 embedding=COALESCE(excluded.embedding, facts.embedding),
                 use_count=facts.use_count+1,
                 updated_at=excluded.updated_at,
                 last_used=excluded.last_used""",
            (narsese, english, source, frequency, confidence, pack_embedding(embedding), now, now, now),
        )
        self._db.commit()

    # ── conversational memory (ADR-008 + ADR-009) — ranked, supersedable English store ──
    def remember(self, text: str, source: str | None = None,
                 embedding: list[float] | None = None, now: float | None = None) -> bool:
        """Persist one auto-extracted English memory and resolve single-valued-slot conflicts.

        Idempotent on exact text (re-stating reactivates + bumps usage). Stores `embedding` for
        ranked retrieval. After writing, supersedes any active memory that fills the SAME
        single-valued slot with a DIFFERENT value (ADR-009) — the new memory wins. Returns True iff
        this created a genuinely NEW memory (so callers acknowledge only new saves)."""
        now = time.time() if now is None else now
        is_new = self._db.execute("SELECT 1 FROM memories WHERE text=?", (text,)).fetchone() is None
        self._db.execute(
            """INSERT INTO memories (text, source, embedding, use_count, active,
                                     created_at, updated_at, last_used)
               VALUES (?,?,?,1,1,?,?,?)
               ON CONFLICT(text) DO UPDATE SET
                 source=COALESCE(excluded.source, memories.source),
                 embedding=COALESCE(excluded.embedding, memories.embedding),
                 active=1, superseded_by=NULL, superseded_at=NULL,
                 use_count=memories.use_count+1,
                 updated_at=excluded.updated_at,
                 last_used=excluded.last_used""",
            (text, source, pack_embedding(embedding), now, now, now),
        )
        new_id = self._db.execute("SELECT id FROM memories WHERE text=?", (text,)).fetchone()[0]
        self._resolve_conflicts(new_id, text, embedding, now)
        self._db.commit()
        return is_new

    def search(self, query_vec: list[float], k: int = 8, threshold: float = 0.0) -> list[str]:
        """Embedding-ranked retrieval (ADR-009): the top-k ACTIVE memories most relevant to the
        query, by cosine, with pinned memories always included. Replaces the recency dump as the
        injection set so a growing table neither overflows context nor drowns out relevance."""
        rows = self._db.execute(
            "SELECT text, embedding, pinned FROM memories WHERE active=1").fetchall()
        scored = [(_cosine(query_vec, v), t)
                  for (t, e, _p) in rows if (v := unpack_embedding(e)) is not None]
        scored.sort(key=lambda st: st[0], reverse=True)
        top = [t for (s, t) in scored[:k] if s >= threshold]
        out: list[str] = []
        for t in [t for (t, _e, p) in rows if p] + top:   # pinned first, then ranked
            if t not in out:
                out.append(t)
        return out

    def memories_for_recall(self, limit: int = 30) -> list[str]:
        """Recency fallback (no embedder wired): ACTIVE memories, pinned first then most recent."""
        rows = self._db.execute(
            "SELECT text FROM memories WHERE active=1 ORDER BY pinned DESC, last_used DESC LIMIT ?",
            (limit,)).fetchall()
        return [r[0] for r in rows]

    def _resolve_conflicts(self, new_id: int, text: str, embedding: list[float] | None,
                           now: float) -> None:
        """Supersede active memories that fill the same single-valued slot with a different value.
        Cosine pre-filters candidates when an embedding is present (scale); the slot check decides."""
        if slot_of(text) is None:
            return  # no single-valued slot -> keep both (multi-valued or unknown predicate)
        for rid, rtext, remb in self._db.execute(
                "SELECT id, text, embedding FROM memories WHERE active=1 AND id!=?", (new_id,)):
            if embedding is not None:
                cand = unpack_embedding(remb)
                if cand is not None and _cosine(embedding, cand) < _CONFLICT_CANDIDATE_COSINE:
                    continue
            if same_single_valued_slot(text, rtext):
                self._supersede(rid, by=new_id, now=now)

    def _supersede(self, older_id: int, by: int, now: float) -> None:
        self._db.execute(
            "UPDATE memories SET active=0, superseded_by=?, superseded_at=? WHERE id=?",
            (by, now, older_id))

    def forget(self, text: str, now: float | None = None) -> int:
        """Soft-delete: tombstone an active memory (no auto-fallback — the slot goes empty). The row
        is kept for audit/undo. Returns rows deactivated. `restore` brings it back."""
        now = time.time() if now is None else now
        cur = self._db.execute(
            "UPDATE memories SET active=0, updated_at=? WHERE text=? AND active=1", (now, text))
        self._db.commit()
        return cur.rowcount

    def forget_normalized(self, text: str, now: float | None = None) -> int:
        """Soft-delete the first ACTIVE memory whose normalized text matches `text` (case/whitespace/
        punctuation-insensitive). Fallback for `forget` when phrasing differs slightly. No fuzzy/
        semantic match — see Jarvis._forget_facts for why that is unsafe with similar siblings."""
        now = time.time() if now is None else now
        target = _norm_text(text)
        for rid, rtext in self._db.execute("SELECT id, text FROM memories WHERE active=1"):
            if _norm_text(rtext) == target:
                self._db.execute("UPDATE memories SET active=0, updated_at=? WHERE id=?", (now, rid))
                self._db.commit()
                return 1
        return 0

    def restore(self, text: str, now: float | None = None) -> bool:
        """Reactivate a tombstoned memory, tombstoning whatever currently fills its slot (preserves
        the ≤1-active-per-slot invariant). Returns True if a row was reactivated."""
        now = time.time() if now is None else now
        row = self._db.execute("SELECT id, text FROM memories WHERE text=?", (text,)).fetchone()
        if row is None:
            return False
        mid, mtext = row
        if slot_of(mtext) is not None:  # evict the current slot holder, if any
            for rid, rtext in self._db.execute(
                    "SELECT id, text FROM memories WHERE active=1 AND id!=?", (mid,)):
                if same_single_valued_slot(mtext, rtext):
                    self._supersede(rid, by=mid, now=now)
        self._db.execute(
            "UPDATE memories SET active=1, superseded_by=NULL, superseded_at=NULL, updated_at=? "
            "WHERE id=?", (now, mid))
        self._db.commit()
        return True

    def undo_supersede(self, text: str, now: float | None = None) -> bool:
        """Undo the supersession in which `text` was the superseder (C): reactivate C's IMMEDIATE
        predecessor (the row whose superseded_by points at C) and tombstone C. One hop — never a
        transitive cascade (that would resurrect two mutually-exclusive values). Returns True if a
        predecessor was reactivated."""
        now = time.time() if now is None else now
        row = self._db.execute("SELECT id FROM memories WHERE text=?", (text,)).fetchone()
        if row is None:
            return False
        cid = row[0]
        pred = self._db.execute(
            "SELECT id FROM memories WHERE superseded_by=? ORDER BY superseded_at DESC LIMIT 1",
            (cid,)).fetchone()
        if pred is None:
            return False
        self._db.execute(
            "UPDATE memories SET active=1, superseded_by=NULL, superseded_at=NULL, updated_at=? "
            "WHERE id=?", (now, pred[0]))
        self._db.execute("UPDATE memories SET active=0, updated_at=? WHERE id=?", (now, cid))
        self._db.commit()
        return True

    def touch_usage(self, narsese: str, use_count: int, last_used: float) -> None:
        """Snapshot reconciliation of usage/recency (use_count mirrors ONA's usage signal)."""
        self._db.execute("UPDATE facts SET use_count=?, last_used=? WHERE narsese=?",
                         (use_count, last_used, narsese))
        self._db.commit()

    def pin(self, narsese: str, priority_tier: int = 1) -> None:
        self._db.execute("UPDATE facts SET pinned=1, priority_tier=? WHERE narsese=?",
                         (priority_tier, narsese))
        self._db.commit()

    def unpin(self, narsese: str) -> None:
        self._db.execute("UPDATE facts SET pinned=0 WHERE narsese=?", (narsese,))
        self._db.commit()

    def get(self, narsese: str) -> Fact | None:
        row = self._db.execute(f"SELECT {_COLS} FROM facts WHERE narsese=?", (narsese,)).fetchone()
        return _row_to_fact(row) if row else None

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def facts_for_reload(self, limit: int = 40, now: float | None = None) -> list[Fact]:
        """Cache-miss repopulation order (v1.24.0 Step 3): the highest-VALUE beliefs win the top_k slots
        that get loaded into the ONA L1 bag. Pinned beliefs rank above all (they are protected and never
        decay); among the rest, ORDER BY V(b) — so a corroborated, recently-used belief outranks a stale,
        floor-confidence passive observation. Tombstoned rows (active=0) are excluded entirely."""
        now = time.time() if now is None else now
        rows = self._db.execute(
            f"SELECT {_COLS} FROM facts WHERE active=1 "
            f"ORDER BY pinned DESC, priority_tier DESC, {_value_sql()} DESC, last_used DESC LIMIT ?",
            (now, limit)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def facts_matching(self, atoms: list[str]) -> list[Fact]:
        """ADR-056/Gate 2: term-scoped candidate fetch — every fact whose Narsese contains ANY of `atoms`,
        via the FTS5 index (C-speed, complete at any store size; no recency cap, no full-table sort). Atoms
        are quoted and OR-joined so one that happens to be `or`/`and`/`near` can't collide with FTS5 query
        syntax (embedded quotes stripped — atoms are `[a-z0-9_]`, but defended anyway)."""
        terms = [a.replace('"', "") for a in atoms if a and a.replace('"', "").strip()]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self._db.execute(
            f"SELECT {_COLS} FROM facts WHERE active=1 "
            "AND id IN (SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?)",
            (match,)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def sweep_passive(self, now: float | None = None) -> int:
        """The v1.24.0 Step 3 decay sweep — reversibly TOMBSTONE (active=0) stale passive noise so the L2
        store doesn't become a graveyard for transient, never-recalled observations. Targets a row ONLY
        when EVERY guard holds (the 'Moderate' predicate, ratified 2026-06-13):

          source='passive'    — never touches a told / NULL-trusted / cloud belief
          active=1            — don't re-tombstone
          pinned=0            — pinned beliefs are protected
          use_count <= 1      — never recalled or corroborated (any re-observation bumps use_count -> spared)
          unused > 30 days    — a full month's grace to query/corroborate the document it came from
          V(b) < 0.18         — value backstop (a floor-confidence passive belief; corroboration raises V)

        Soft-delete, not DROP: a false positive is fully recoverable. Returns the count tombstoned. This is
        a single set-based UPDATE — cheap even on a large L2 — but it is the daemon's job to call it OFF the
        select() loop (the overnight runner's idle-maintenance hook), never on the hot path."""
        now = time.time() if now is None else now
        cur = self._db.execute(
            f"""UPDATE facts SET active=0, updated_at=?
                WHERE active=1 AND pinned=0 AND source='passive' AND use_count<=1
                  AND ((? - last_used) / 86400.0) > {_SWEEP_AGE_DAYS}
                  AND {_value_sql()} < {_SWEEP_V_THETA}""",
            (now, now, now))
        self._db.commit()
        return cur.rowcount

    def prune(self, max_rows: int) -> int:
        """Evict least-useful UNPINNED rows when over capacity. Pinned rows are immune."""
        over = self.count() - max_rows
        if over <= 0:
            return 0
        cur = self._db.execute(
            """DELETE FROM facts WHERE id IN (
                 SELECT id FROM facts WHERE pinned=0
                 ORDER BY priority_tier ASC, use_count ASC, last_used ASC LIMIT ?)""", (over,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        self._db.close()
