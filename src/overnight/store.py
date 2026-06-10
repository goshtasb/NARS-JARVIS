"""Durable overnight state (ADR-031) — Imperative Shell (S-02). Two cohesive tables on the shared
`jarvis.db`: the incoming task QUEUE and the outgoing HELD-ledger of actions awaiting morning approval.

Unlike the ADR-020 consent ledger (pure, in-memory — wiped on daemon recycle), these SURVIVE a restart:
a queued night of work, and the actions held for your approval, persist until you act on them. Mirrors
`habits/store.py` (schema constant, shared db_path). New tables only, so `CREATE TABLE IF NOT EXISTS`
is the whole migration story for v1.
"""
from __future__ import annotations

import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS overnight_queue (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  action      TEXT NOT NULL,
  arg         TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | held | failed
  result      TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS held_ledger (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     INTEGER,
  action      TEXT NOT NULL,
  arg         TEXT NOT NULL DEFAULT '',
  reason      TEXT NOT NULL DEFAULT '',
  disposition TEXT NOT NULL DEFAULT 'held',      -- held | approved | denied
  created_at  REAL NOT NULL
);
"""


def _now(now: float | None) -> float:
    return time.time() if now is None else now


class OvernightQueue:
    """The committed batch of tasks (each a concrete catalog action + arg) to work through unattended."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def enqueue(self, action: str, arg: str = "", now: float | None = None) -> int:
        t = _now(now)
        cur = self._db.execute(
            "INSERT INTO overnight_queue(action,arg,status,created_at,updated_at) VALUES(?,?,'pending',?,?)",
            (action, arg, t, t))
        self._db.commit()
        return cur.lastrowid

    def next_pending(self) -> dict | None:
        row = self._db.execute(
            "SELECT id,action,arg FROM overnight_queue WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        return {"id": row[0], "action": row[1], "arg": row[2]} if row else None

    def mark(self, task_id: int, status: str, result: str = "", now: float | None = None) -> None:
        self._db.execute("UPDATE overnight_queue SET status=?, result=?, updated_at=? WHERE id=?",
                         (status, result, _now(now), task_id))
        self._db.commit()

    def reset_running(self) -> None:
        """Restart safety: a task left 'running' by a 3 AM crash reverts to 'pending' — never a zombie."""
        self._db.execute("UPDATE overnight_queue SET status='pending' WHERE status='running'")
        self._db.commit()

    def list_all(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,action,arg,status,result FROM overnight_queue ORDER BY id").fetchall()
        return [dict(zip(("id", "action", "arg", "status", "result"), r)) for r in rows]

    def counts(self) -> dict:
        rows = self._db.execute("SELECT status, COUNT(*) FROM overnight_queue GROUP BY status").fetchall()
        return {s: c for s, c in rows}

    def close(self) -> None:
        self._db.close()


class HeldLedger:
    """The durable list of actions the runner refused to run unattended — they wait for your morning OK."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def hold(self, task_id: int, action: str, arg: str = "", reason: str = "",
             now: float | None = None) -> int:
        cur = self._db.execute(
            "INSERT INTO held_ledger(task_id,action,arg,reason,disposition,created_at) "
            "VALUES(?,?,?,?,'held',?)", (task_id, action, arg, reason, _now(now)))
        self._db.commit()
        return cur.lastrowid

    def pending(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,task_id,action,arg,reason FROM held_ledger WHERE disposition='held' ORDER BY id"
        ).fetchall()
        return [dict(zip(("id", "task_id", "action", "arg", "reason"), r)) for r in rows]

    def get(self, hid: int) -> dict | None:
        row = self._db.execute(
            "SELECT id,task_id,action,arg,reason,disposition FROM held_ledger WHERE id=?", (hid,)).fetchone()
        return dict(zip(("id", "task_id", "action", "arg", "reason", "disposition"), row)) if row else None

    def resolve(self, hid: int, accepted: bool) -> None:
        """Mark a held action approved/denied. Idempotent: only a still-'held' row changes."""
        self._db.execute("UPDATE held_ledger SET disposition=? WHERE id=? AND disposition='held'",
                         ("approved" if accepted else "denied", hid))
        self._db.commit()

    def list_all(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,task_id,action,arg,reason,disposition FROM held_ledger ORDER BY id").fetchall()
        return [dict(zip(("id", "task_id", "action", "arg", "reason", "disposition"), r)) for r in rows]

    def close(self) -> None:
        self._db.close()
