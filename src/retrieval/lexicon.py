"""The L2 lexicon index (ADR-056 / Gate 2) — Imperative Shell (S-02): the durable map that lets Stage 1
resolve a query's surface mentions onto the graph's historical namespace **deterministically**, before
the embedder is ever consulted.

Two tables:
- `lexicon_terms`   — every canonical atomic term ever ingested, with frequency + first/last seen.
- `lexicon_aliases` — surface form -> canonical term (e.g. `sol` -> `solana`), captured at ingest when
  source text said "SOL" but extraction codified `solana`. This is the deterministic bridge that
  dissolves the Stage-0 catch-22: the model proposes surface mentions, the lexicon disposes the namespace.

Both keys are stored `atom()`-normalized so lookups from Stage 0 (also `atom()`-normalized) match exactly.
Resolution order is the caller's contract (exact term first, then alias, then — elsewhere — the embedder);
this store only owns the deterministic two tiers and never guesses.
"""
from __future__ import annotations

import sqlite3

from shared import atom

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lexicon_terms (
    term       TEXT PRIMARY KEY,
    freq       INTEGER NOT NULL DEFAULT 0,
    first_seen REAL    NOT NULL,
    last_seen  REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS lexicon_aliases (
    alias TEXT    NOT NULL,
    term  TEXT    NOT NULL,
    freq  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (alias, term)
);
CREATE INDEX IF NOT EXISTS idx_lexicon_alias ON lexicon_aliases(alias);
"""


class LexiconStore:
    def __init__(self, db_path: str = "jarvis.db") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # ── ingest-time population (incremental; called as the graph learns) ──
    def record_term(self, term: str, *, now: float) -> str:
        """Register/strengthen a canonical term. Returns the normalized key actually stored."""
        key = atom(term)
        self._db.execute(
            "INSERT INTO lexicon_terms(term, freq, first_seen, last_seen) VALUES (?,1,?,?) "
            "ON CONFLICT(term) DO UPDATE SET freq = freq + 1, last_seen = excluded.last_seen",
            (key, now, now),
        )
        self._db.commit()
        return key

    def record_alias(self, alias: str, term: str, *, now: float) -> None:
        """Map a surface form onto a canonical term (e.g. `SOL` -> `solana`). Also registers the term."""
        a, key = atom(alias), self.record_term(term, now=now)
        if a == "_" or a == key:                       # an alias identical to its term carries no signal
            return
        self._db.execute(
            "INSERT INTO lexicon_aliases(alias, term, freq) VALUES (?,?,1) "
            "ON CONFLICT(alias, term) DO UPDATE SET freq = freq + 1",
            (a, key),
        )
        self._db.commit()

    # ── Stage-1 deterministic resolution (exact, then alias) ──
    def resolve_exact(self, mention: str) -> str | None:
        row = self._db.execute("SELECT term FROM lexicon_terms WHERE term = ?", (atom(mention),)).fetchone()
        return row[0] if row else None

    def resolve_alias(self, mention: str) -> list[str]:
        """Terms this surface form maps to, most-frequent first (a surface form may be ambiguous)."""
        rows = self._db.execute(
            "SELECT term FROM lexicon_aliases WHERE alias = ? ORDER BY freq DESC, term ASC",
            (atom(mention),),
        ).fetchall()
        return [r[0] for r in rows]

    def resolve(self, mention: str) -> str | None:
        """Convenience: exact term wins; else the top alias; else None (the caller falls to the embedder)."""
        exact = self.resolve_exact(mention)
        if exact is not None:
            return exact
        aliases = self.resolve_alias(mention)
        return aliases[0] if aliases else None

    # ── inspection ──
    def term_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM lexicon_terms").fetchone()[0]

    def close(self) -> None:
        self._db.close()
