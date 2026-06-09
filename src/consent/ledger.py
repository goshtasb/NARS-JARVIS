"""The ConsentLedger (ADR-020). Functional Core (S-02) — pure, in-memory, I/O-free.

The registry of OPEN consent requests: the "suspended execution state" as data on the heap, NOT a
blocked call stack — so the daemon's single select loop never stalls waiting on a human. Holds only
open requests; `resolve`/`expire_due` are one-shot pops, which is what makes a resolve idempotent (a
replayed click hits an absent id and is a safe no-op) and a double-execute impossible.
"""
from __future__ import annotations

from dataclasses import replace

from .request import APPROVED, DENIED, EXPIRED, ConsentRequest


class ConsentLedger:
    """In-memory map of open requests, keyed by id. Pure of I/O — fully unit-testable."""

    def __init__(self) -> None:
        self._open: dict[int, ConsentRequest] = {}

    def open(self, req: ConsentRequest) -> ConsentRequest:
        """Register a new open request (the shell has already minted its id/deadline)."""
        self._open[req.id] = req
        return req

    def get(self, rid: int) -> ConsentRequest | None:
        """The open request for `rid`, or None once it has been resolved/expired (one-shot)."""
        return self._open.get(rid)

    def resolve(self, rid: int, accepted: bool) -> ConsentRequest | None:
        """Pop `rid` and stamp it APPROVED/DENIED. Returns the closed request, or None if it was
        unknown/already closed — the basis for idempotent resolve (no double-execute)."""
        req = self._open.pop(rid, None)
        if req is None:
            return None
        return replace(req, status=APPROVED if accepted else DENIED)

    def expire_due(self, now: float) -> list[ConsentRequest]:
        """Pop every open request past its deadline at `now`; return them stamped EXPIRED."""
        due = [r for r in self._open.values() if r.is_due(now)]
        for r in due:
            self._open.pop(r.id, None)
        return [replace(r, status=EXPIRED) for r in due]

    def snapshot(self) -> list[dict]:
        """Wire-safe view of all currently-open requests — the payload for `consent_sync` so a
        (re)connecting client can reconcile its UI against the authoritative open-set."""
        return [r.to_public() for r in self._open.values()]
