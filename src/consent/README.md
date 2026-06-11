# consent

## Overview
The unified interactive-consent state machine (ADR-020) — one continuation-passing abstraction for
every "ask the human, then act" flow (Sentinel training answers, destructive actions, GUI actuation),
replacing what used to be three ad-hoc token systems. This package is the **pure core**: the request
record and the ledger of open requests. The imperative shell that holds the executable on-approve
continuations, emits prompts to the UI, and sweeps expirations lives in `service/consent_service.py`.

## Usage
```python
from consent import ConsentLedger, ConsentRequest, APPROVED, DENIED

ledger = ConsentLedger()
req = ledger.open(ConsentRequest(...))      # -> the opened request (carries its id + state OPEN)
ledger.resolve(req.rid, accepted=True)      # the service layer then runs the held continuation
```

## Key Components
- `request.py` — the immutable `ConsentRequest` record + the resolution states (`APPROVED`/`DENIED`/…).
- `ledger.py` — `ConsentLedger`: the open-request table (open/resolve/expire/snapshot). Pure — no
  callbacks, no I/O; continuations never enter this package.

## Dependencies
Standard library only. `service/consent_service.py` is the only shell over this core.

## Related ADRs
ADR-020 (unified consent). Consumed by ADR-019/021 (gated actions), ADR-031 (held overnight actions).
