"""consent — the unified interactive-consent state machine (ADR-020).

One continuation-passing abstraction for every "ask the human, then act" flow (Sentinel training,
destructive actions, action confirmation), replacing three ad-hoc token systems. The pure core lives
here (the `ConsentRequest` record + the `ConsentLedger` of open requests); the imperative shell that
holds the executable continuations and emits/sweeps lives in `service.consent_service`.

Public interface (ADR-001: a module's surface is its `__init__.py` + `__all__`).
"""
from .ledger import ConsentLedger
from .request import (
    APPROVED,
    DEFAULT_APPROVE,
    DEFAULT_DENY,
    DENIED,
    EXPIRED,
    OPEN,
    ConsentRequest,
)

__all__ = [
    "ConsentRequest",
    "ConsentLedger",
    "OPEN",
    "APPROVED",
    "DENIED",
    "EXPIRED",
    "DEFAULT_DENY",
    "DEFAULT_APPROVE",
]
