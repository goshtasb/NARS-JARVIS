"""Persistent grounding store (L2) — canonical atoms + surface aliases. Imperative Shell (S-02).

Turns the ephemeral entity-resolution cache into concrete SQLite so "ducks" still means "duck"
after a restart. Three traps, solved without a vector DB:
  - Dependency: brute-force cosine as ONE numpy matmul over an in-RAM unit matrix. A single user's
    atom vocabulary is small (hundreds-thousands); an ANN index would be negative ROI + a C-extension.
  - Latency: vectors load as ONE bulk `np.frombuffer` (not per-row unpack), LAZILY on the first
    cache call (any of resolve/nearest), so the REPL prompt renders instantly.
  - Compute: the `aliases` table memoizes surface->canonical, so the embedder runs at most ONCE per
    novel surface form, ever.

Vectors are UNIT-NORMALIZED at this boundary (never trusting the caller), so cosine == dot product.
numpy is imported lazily (it ships with llama-cpp-python, i.e. it is present wherever embeddings are).
"""
from __future__ import annotations

import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS atoms (
    name       TEXT PRIMARY KEY,         -- canonical concept, e.g. "duck"
    embedding  BLOB NOT NULL,            -- float32, unit-normalized at write
    dim        INTEGER NOT NULL,
    use_count  INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    surface    TEXT PRIMARY KEY,         -- raw surface form, e.g. "ducks"
    canonical  TEXT NOT NULL,            -- -> atoms.name
    created_at REAL NOT NULL
);
"""


class SqliteGroundingStore:
    """Canonical-atom + alias persistence with an in-RAM unit matrix for similarity.

    Satisfies the grounding-cache shape the Translator depends on:
    resolve_surface / nearest / add_atom / add_alias.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._loaded = False
        self._aliases: dict[str, str] = {}
        self._names: list[str] = []
        self._name_set: set[str] = set()
        self._matrix = None  # np.ndarray (n, dim), unit-normalized — built lazily
        self._dim: int | None = None

    # ── lazy bulk load (fires on the FIRST cache call, any command) ──
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._aliases = dict(self._db.execute("SELECT surface, canonical FROM aliases").fetchall())
        rows = self._db.execute("SELECT name, embedding FROM atoms").fetchall()
        self._names = [r[0] for r in rows]
        self._name_set = set(self._names)
        if rows:
            import numpy as np
            self._dim = len(rows[0][1]) // 4  # float32 = 4 bytes
            buf = b"".join(r[1] for r in rows)  # ONE contiguous read, not per-row unpack
            self._matrix = np.frombuffer(buf, dtype=np.float32).reshape(len(rows), self._dim).copy()
        self._loaded = True

    @staticmethod
    def _unit(vec) -> "object":
        """Normalize at the boundary — never trust the caller's vector."""
        import numpy as np
        v = np.asarray(vec, dtype=np.float32).ravel()
        norm = float(np.linalg.norm(v))
        return (v / norm).astype(np.float32) if norm > 0.0 else v

    # ── lookup ──
    def resolve_surface(self, surface: str) -> str | None:
        """Exact memo: an alias hit OR an already-canonical name. None if unknown. No embedding."""
        self._ensure_loaded()
        if surface in self._aliases:
            return self._aliases[surface]
        if surface in self._name_set:
            return surface
        return None

    def nearest(self, query_vec, threshold: float) -> str | None:
        """Nearest canonical atom by cosine (one matmul); None if below threshold or empty."""
        self._ensure_loaded()
        if self._matrix is None or not self._names:
            return None
        import numpy as np
        q = self._unit(query_vec)
        if q.shape[0] != self._matrix.shape[1]:
            return None
        sims = self._matrix @ q  # unit matrix · unit query == cosine
        i = int(sims.argmax())
        return self._names[i] if float(sims[i]) >= threshold else None

    # ── mutation (persist + update RAM incrementally; never reload) ──
    def add_atom(self, name: str, raw_vec) -> None:
        """Persist a NEW canonical atom; normalize at the boundary. Idempotent on name."""
        self._ensure_loaded()
        import numpy as np
        v = self._unit(raw_vec)
        self._db.execute(
            "INSERT OR IGNORE INTO atoms(name, embedding, dim, use_count, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, v.tobytes(), int(v.shape[0]), 1, time.time()),
        )
        self._db.commit()
        if name not in self._name_set:
            self._names.append(name)
            self._name_set.add(name)
            self._dim = int(v.shape[0])
            row = v.reshape(1, -1)
            self._matrix = row if self._matrix is None else np.vstack([self._matrix, row])

    def add_alias(self, surface: str, canonical: str) -> None:
        """Persist a surface->canonical memo (the embedder won't run on `surface` again)."""
        self._ensure_loaded()
        self._db.execute(
            "INSERT OR REPLACE INTO aliases(surface, canonical, created_at) VALUES (?,?,?)",
            (surface, canonical, time.time()),
        )
        self._db.commit()
        self._aliases[surface] = canonical

    def counts(self) -> tuple[int, int]:
        """(num canonical atoms, num aliases) — for diagnostics/tests."""
        self._ensure_loaded()
        return len(self._names), len(self._aliases)

    def close(self) -> None:
        self._db.close()
