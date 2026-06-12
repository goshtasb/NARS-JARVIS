"""The overnight batch runner (ADR-031, ADR-052) — Imperative Shell (S-02).

Deterministically works through a committed queue of concrete catalog actions, one task per daemon
tick (so it never monopolises the single-threaded select loop). Each task is classified by the hard
read-only boundary (`overnight.safe_autonomous`): a read-only action RUNS unattended; anything that
mutates state, drives the GUI, or is destructive is HELD in the durable ledger for explicit morning
approval. There is **no LLM in this loop** — explicit-commit means tasks are concrete, so orchestration
stays deterministic (code disposes).

ADR-052: a *heavy* read-only task (`summarize_file` — a minutes-long Map-Reduce over many 7B calls)
is NOT run inline (that would freeze the loop, blinding the Sentinel). It is OFFLOADED to a detached
CPU worker (`SummaryJob`) whose stdout the daemon's select() multiplexes via `extra_fds`/`handle_fd`,
exactly like a voice transcription. Light tasks (reports, file reads, web egress) still run inline.
"""
from __future__ import annotations

from typing import Callable

from actions import resolve
from overnight import safe_autonomous

from .summary_job import SummaryJob

# Heavy actions that must run off the loop (detached worker), keyed to the SummaryJob construct.
_OFFLOAD = {"summarize_file"}


class OvernightRunner:
    def __init__(self, queue, ledger, action_runner, emit: Callable[[str, dict], None],
                 make_job: Callable[..., object] = SummaryJob) -> None:
        self._queue = queue
        self._ledger = ledger
        self._actions = action_runner          # ActionRunner: .perform(name, arg) -> str
        self._emit = emit
        self._make_job = make_job              # injectable for tests; default spawns the real worker
        self._active = False
        self._job = None                       # the in-flight offloaded SummaryJob (None when idle)

    @property
    def active(self) -> bool:
        return self._active or self._job is not None

    def start(self) -> int:
        """Begin (or resume) draining the queue. Reverts any crash-orphaned 'running' rows to pending."""
        self._queue.reset_running()
        pending = sum(1 for r in self._queue.list_all() if r["status"] == "pending")
        self._active = pending > 0
        self._emit("overnight_started", {"queued": pending})
        return pending

    def advance(self) -> None:
        """Advance exactly one task. Called from `session.tick()`; a no-op unless started and work remains.
        While an offloaded job is in flight, this is a no-op — the job finalizes via `handle_fd`."""
        if self._job is not None:                           # an offloaded summary is still running
            return
        if not self._active:
            return
        task = self._queue.next_pending()
        if task is None:                                    # queue drained -> done
            self._active = False
            self._emit("overnight_done", self._tally())
            return
        tid, name, arg = task["id"], task["action"], task["arg"]
        action = resolve(name)
        if not safe_autonomous(action):                     # mutating/unknown -> held for morning approval
            reason = "unknown action" if action is None else f"{action.kind} requires approval"
            self._ledger.hold(tid, name, arg, reason=reason)
            self._queue.mark(tid, "held")
            self._emit("overnight_progress", {"id": tid, "action": name, "status": "held"})
            return
        if name in _OFFLOAD:                                 # heavy -> detached worker, never inline
            self._spawn_offload(tid, name, arg)
            return
        self._run_inline(tid, name, arg)                    # light read-only -> run on the tick

    def _run_inline(self, tid: int, name: str, arg: str) -> None:
        self._queue.mark(tid, "running")
        try:
            result = self._actions.perform(name, arg)
        except Exception as exc:  # noqa: BLE001 — one bad task must not kill the night
            self._queue.mark(tid, "failed", result=str(exc))
            self._emit("overnight_progress", {"id": tid, "action": name, "status": "failed"})
            return
        # A read-only action REPORTS errors as an `[ERROR: …]` string (it never raises), so the except
        # above can't catch them. Without this check a failed task was stamped "done" and the error
        # silently swallowed — the exact "I queued it and nothing happened" bug.
        status = "failed" if result.lstrip().startswith("[ERROR") else "done"
        self._queue.mark(tid, status, result=result)
        self._emit("overnight_progress", {"id": tid, "action": name, "status": status})

    def _spawn_offload(self, tid: int, name: str, arg: str) -> None:
        try:
            self._job = self._make_job(arg, tid, action=name)
        except Exception as exc:  # noqa: BLE001 — if the worker can't even start, fail the task loudly
            self._queue.mark(tid, "failed", result=f"[ERROR: could not start summary worker: {exc}]")
            self._emit("overnight_progress", {"id": tid, "action": name, "status": "failed"})
            return
        self._queue.mark(tid, "running", result="summarizing… (starting)")
        self._emit("overnight_progress", {"id": tid, "action": name, "status": "running"})

    # ── select-loop seam for the offloaded worker (mirrors the voice-job hooks) ──
    def extra_fds(self) -> list[int]:
        return [self._job.fileno()] if self._job is not None else []

    def handle_fd(self, fd: int) -> None:
        job = self._job
        if job is None or fd != job.fileno():
            return
        for tag, payload in job.read():
            if tag == "progress" and isinstance(payload, dict):
                i, n = payload.get("i", "?"), payload.get("n", "?")
                self._queue.mark(job.task_id, "running", result=f"summarizing… chunk {i}/{n}")
                self._emit("overnight_progress",
                           {"id": job.task_id, "action": job.action, "status": "running",
                            "detail": f"{i}/{n}"})
            elif tag == "result":
                self._queue.mark(job.task_id, "done", result=str(payload))
                self._emit("overnight_progress",
                           {"id": job.task_id, "action": job.action, "status": "done"})
            elif tag == "error":
                self._queue.mark(job.task_id, "failed", result=f"[ERROR: {payload}]")
                self._emit("overnight_progress",
                           {"id": job.task_id, "action": job.action, "status": "failed"})
            elif tag == "eof":
                job.cleanup()
                self._job = None                            # next tick resumes draining the queue

    def _tally(self) -> dict:
        c = self._queue.counts()
        return {"done": c.get("done", 0), "held": c.get("held", 0), "failed": c.get("failed", 0)}
