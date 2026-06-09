"""The ConsentRequest record (ADR-020). Functional Core (S-02) — pure, frozen, I/O-free.

One pending "ask the human, then act" decision, modelled as immutable data. Crucially the record
carries NO executable payload — only an opaque id, a human prompt, and lifecycle metadata. The
continuation (what actually runs on approve/deny) lives server-side in `service.consent_service`,
keyed by `id`. That separation is the opaque-ID security boundary: what crosses the wire to the
client can never name an executable, only vote on a pre-validated intent.
"""
from __future__ import annotations

from dataclasses import dataclass

# Lifecycle states. A request is created OPEN and ends in exactly one terminal state.
OPEN, APPROVED, DENIED, EXPIRED = "open", "approved", "denied", "expired"

# What a timeout means for THIS request. Pre-action consent defaults to DENY (don't act on silence);
# a post-action undo prompt would default to APPROVE (keep — the action already happened).
DEFAULT_DENY, DEFAULT_APPROVE = "deny", "approve"


@dataclass(frozen=True)
class ConsentRequest:
    """An immutable pending consent. `expiry_default` decides the safe outcome on timeout."""
    id: int
    kind: str                 # "intervention" | "action" | … (producer-defined, for the client's label)
    prompt: str               # human-facing question
    label: str                # short description of the intent (for acks / logs)
    created_at: float
    expires_at: float
    expiry_default: str = DEFAULT_DENY
    status: str = OPEN

    def is_open(self) -> bool:
        return self.status == OPEN

    def is_due(self, now: float) -> bool:
        """True if this open request has reached its deadline at `now`."""
        return self.status == OPEN and now >= self.expires_at

    def to_public(self) -> dict:
        """The wire-safe view sent to clients — id + display + deadline, never the continuation."""
        return {"id": self.id, "kind": self.kind, "prompt": self.prompt,
                "label": self.label, "expires_at": self.expires_at}
