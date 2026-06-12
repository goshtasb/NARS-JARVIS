"""The single sanctioned SQLite connection bootstrap (ADR-052) — Imperative Shell (S-02).

Every store on the shared `jarvis.db` (memory, habits, sentinel, persona, overnight, grounding,
metrics) opens through here so they all get the SAME durability posture:

- `journal_mode=WAL` — Write-Ahead Logging lets readers and a single writer proceed CONCURRENTLY.
  Under the default rollback journal, a write takes a whole-database lock that excludes every reader;
  the Sentinel writing `usage_events` on each app-switch would serialize against the overnight
  queue / memory reads and could raise `database is locked`. WAL is a one-time, persistent change in
  the database header — setting it on each connection is idempotent (it just reports the mode).
- `busy_timeout=5000` — when a lock IS contended (two writers), wait up to 5 s for it to clear
  instead of raising immediately. Per-connection; must be set every open.

A `:memory:` database can't use WAL (no file to back the log); the PRAGMA is a harmless no-op there,
so this helper is safe for the in-memory databases the tests use.
"""
from __future__ import annotations

import sqlite3


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open `path` with the project's standard WAL + busy_timeout posture. Drop-in for sqlite3.connect."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")     # readers + one writer run concurrently (no-op on :memory:)
    db.execute("PRAGMA busy_timeout=5000")    # wait up to 5s for a contended lock instead of raising
    return db
