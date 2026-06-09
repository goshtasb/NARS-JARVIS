"""The consent shell (ADR-020) — Imperative Shell (S-02). Holds the executable continuations.

Wraps the pure `ConsentLedger` with the three things that can't be pure: the on-approve / on-negative
**continuations** (kept here, keyed by id — NEVER on the wire), event emission, and the wall clock.
This is the single owner of "ask the human, then act" for every producer (Sentinel, executor,
destructive actions). It never blocks: `request` returns immediately, the decision arrives later as a
`consent_resolve` command, and unattended requests are reaped by `sweep` on the daemon's tick.
"""
from __future__ import annotations

import time
from typing import Callable

from consent import DEFAULT_APPROVE, DEFAULT_DENY, ConsentLedger, ConsentRequest

EventSink = Callable[[str, dict], None]
Thunk = Callable[[], object]          # on-approve / on-negative continuation; may return a message
_DEFAULT_TTL = 120.0                  # seconds a pending consent waits before default-resolving


class ConsentService:
    """Opens, resolves, and reaps consent requests; runs the matching continuation exactly once."""

    def __init__(self, emit: EventSink, clock: Callable[[], float] = time.time,
                 default_ttl: float = _DEFAULT_TTL) -> None:
        self._emit = emit
        self._clock = clock
        self._ttl = default_ttl
        self._ledger = ConsentLedger()
        self._cont: dict[int, tuple[Thunk | None, Thunk | None]] = {}   # id -> (on_approve, on_negative)
        self._next_id = 1

    def request(self, kind: str, prompt: str, label: str,
                on_approve: Thunk | None = None, on_negative: Thunk | None = None,
                ttl: float | None = None, expiry_default: str = DEFAULT_DENY) -> int:
        """Open a consent request and push a `consent_request` event. Returns the opaque id. Does NOT
        block — the continuation runs later in `resolve`/`sweep`."""
        now = self._clock()
        rid, self._next_id = self._next_id, self._next_id + 1
        req = ConsentRequest(id=rid, kind=kind, prompt=prompt, label=label, created_at=now,
                             expires_at=now + (self._ttl if ttl is None else ttl),
                             expiry_default=expiry_default)
        self._ledger.open(req)
        self._cont[rid] = (on_approve, on_negative)
        self._emit("consent_request", {**req.to_public(), "server_now": now})
        return rid

    def resolve(self, rid: int, accepted: bool) -> str:
        """Run the matching continuation once and close the request. Idempotent: an unknown/already-
        closed id is a safe no-op (the click that raced an expiry just gets a polite message)."""
        req = self._ledger.resolve(rid, accepted)
        if req is None:
            return "no pending request (it may have expired)."
        on_approve, on_negative = self._cont.pop(rid, (None, None))
        msg = self._fire(on_approve if accepted else on_negative)
        self._emit("consent_closed", {"id": rid, "reason": "approved" if accepted else "denied"})
        return msg or (f"approved: {req.label}" if accepted else f"declined: {req.label}")

    def sweep(self, now: float | None = None) -> None:
        """Reap every overdue request, applying its `expiry_default` outcome, and emit `consent_closed`
        so any connected client dismisses the card. Called from the daemon tick — keeps the heap clean
        and guarantees nothing waits forever."""
        for req in self._ledger.expire_due(self._clock() if now is None else now):
            on_approve, on_negative = self._cont.pop(req.id, (None, None))
            self._fire(on_approve if req.expiry_default == DEFAULT_APPROVE else on_negative)
            self._emit("consent_closed", {"id": req.id, "reason": "expired"})

    def snapshot(self) -> dict:
        """The `consent_sync` payload: the authoritative open-set + the server clock, so a (re)connecting
        client reconciles its cards and recomputes each local TTL."""
        return {"requests": self._ledger.snapshot(), "server_now": self._clock()}

    @staticmethod
    def _fire(thunk: Thunk | None) -> str | None:
        """Run a continuation, returning its message; a None thunk or a raised error is swallowed so a
        bad continuation never crashes the daemon loop."""
        if thunk is None:
            return None
        try:
            result = thunk()
        except Exception:  # noqa: BLE001 — a failing continuation must not kill the select loop
            return None
        return result if isinstance(result, str) else None
