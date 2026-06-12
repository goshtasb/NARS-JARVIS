"""The overnight batch runner (ADR-031) — Imperative Shell (S-02).

Deterministically works through a committed queue of concrete catalog actions while you sleep, one task
per daemon tick (so it never monopolises the single-threaded select loop). Each task is classified by
the hard read-only boundary (`overnight.safe_autonomous`): a read-only action RUNS unattended; anything
that mutates state, drives the GUI, or is destructive is HELD in the durable ledger for explicit morning
approval. There is **no LLM in this loop** — explicit-commit means tasks are concrete, so orchestration
stays deterministic (code disposes). Mirrors the tick-advanced pattern of `_drive_agent`/`propose_due`.
"""
from __future__ import annotations

from typing import Callable

from actions import resolve
from overnight import safe_autonomous


class OvernightRunner:
    def __init__(self, queue, ledger, action_runner, emit: Callable[[str, dict], None]) -> None:
        self._queue = queue
        self._ledger = ledger
        self._actions = action_runner          # ActionRunner: .perform(name, arg) -> str
        self._emit = emit
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> int:
        """Begin (or resume) draining the queue. Reverts any crash-orphaned 'running' rows to pending."""
        self._queue.reset_running()
        pending = sum(1 for r in self._queue.list_all() if r["status"] == "pending")
        self._active = pending > 0
        self._emit("overnight_started", {"queued": pending})
        return pending

    def advance(self) -> None:
        """Advance exactly one task. Called from `session.tick()`; a no-op unless started and work remains."""
        if not self._active:
            return
        task = self._queue.next_pending()
        if task is None:                                    # queue drained -> done
            self._active = False
            self._emit("overnight_done", self._tally())
            return
        tid, name, arg = task["id"], task["action"], task["arg"]
        action = resolve(name)
        if safe_autonomous(action):
            self._queue.mark(tid, "running")
            try:
                result = self._actions.perform(name, arg)
            except Exception as exc:  # noqa: BLE001 — one bad task must not kill the night
                self._queue.mark(tid, "failed", result=str(exc))
                self._emit("overnight_progress", {"id": tid, "action": name, "status": "failed"})
                return
            # A read-only action REPORTS errors as an `[ERROR: …]` string (it never raises), so the
            # except above can't catch them. Without this check a failed task was stamped "done" and the
            # error silently swallowed — the exact "I queued it and nothing happened" bug.
            status = "failed" if result.lstrip().startswith("[ERROR") else "done"
            self._queue.mark(tid, status, result=result)
            self._emit("overnight_progress", {"id": tid, "action": name, "status": status})
        else:                                               # held for explicit morning approval
            reason = "unknown action" if action is None else f"{action.kind} requires approval"
            self._ledger.hold(tid, name, arg, reason=reason)
            self._queue.mark(tid, "held")
            self._emit("overnight_progress", {"id": tid, "action": name, "status": "held"})

    def _tally(self) -> dict:
        c = self._queue.counts()
        return {"done": c.get("done", 0), "held": c.get("held", 0), "failed": c.get("failed", 0)}
