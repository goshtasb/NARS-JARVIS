"""Slice 2 I/O shell: the clause_parameters store (SQLite via the project's WAL dbconn).

Persists extracted Parameters per source document so the aggregator can build a per-kind baseline and
exclude a document from its own norm. Re-ingesting a doc_id replaces its rows (idempotent). Model-free.
"""
from __future__ import annotations

import json
import time

import dbconn
from triage.parameter import Parameter

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clause_parameters (
  id INTEGER PRIMARY KEY,
  doc_id        TEXT NOT NULL,          -- stable id (content hash) of the source document
  clause_type   TEXT NOT NULL,
  role          TEXT NOT NULL,
  kind          TEXT NOT NULL,          -- ParameterKind.value
  value         REAL,                   -- NULL when qualitative/unknown
  unit          TEXT,
  canon_lo      REAL,                   -- canonical base (hours for durations); NULL if not canonicalizable
  canon_hi      REAL,                   -- ==canon_lo for EXACT; NULL = open (business days)
  is_qualitative INTEGER NOT NULL,
  raw_quote     TEXT NOT NULL,
  page          INTEGER,
  bbox          TEXT,                   -- json [x0,top,x1,bottom] (provenance for the deviation citation)
  created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clause_params_lookup ON clause_parameters(clause_type, role, kind);
"""

_COLS = ("clause_type", "role", "kind", "value", "unit", "canon_lo", "canon_hi",
         "is_qualitative", "raw_quote", "page", "bbox", "doc_id")


class ParamStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = dbconn.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def add_parameters(self, doc_id: str, params: list[Parameter], now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._db.execute("DELETE FROM clause_parameters WHERE doc_id=?", (doc_id,))   # re-ingest = replace
        for p in params:
            self._db.execute(
                "INSERT INTO clause_parameters(doc_id,clause_type,role,kind,value,unit,canon_lo,canon_hi,"
                "is_qualitative,raw_quote,page,bbox,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, p.clause_type, p.role, p.kind.value, p.value, p.unit, p.canon_lo, p.canon_hi,
                 int(p.is_qualitative), p.raw_quote, p.anchor.page, json.dumps(list(p.anchor.bbox)), now))
        self._db.commit()

    def known_doc_ids(self) -> set[str]:
        """The set of distinct source documents already ingested — Slice 4 dedup (skip a re-dropped folder's
        contracts) and the corpus-size denominator for the bulk-ingest progress state."""
        return {r[0] for r in self._db.execute("SELECT DISTINCT doc_id FROM clause_parameters").fetchall()}

    def rows(self, exclude_doc_id: str | None = None) -> list[dict]:
        sql = f"SELECT {', '.join(_COLS)} FROM clause_parameters"
        args: tuple = ()
        if exclude_doc_id is not None:
            sql += " WHERE doc_id != ?"
            args = (exclude_doc_id,)
        return [dict(zip(_COLS, r)) for r in self._db.execute(sql, args).fetchall()]

    def close(self) -> None:
        self._db.close()
