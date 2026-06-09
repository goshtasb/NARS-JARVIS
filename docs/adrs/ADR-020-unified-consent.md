# ADR-020: Unified interactive consent & state machine

## Status
Accepted. Builds the one abstraction behind three ad-hoc confirm flows, and unblocks destructive
`[[DO:]]` actions (ADR-019 v2) with a native Approve/Deny round-trip. Suite 306 → **330** green.

## Context
Three bespoke "ask the human, then act" mechanisms had grown independently: the Sentinel's
`intervene`, the executor's `act_confirm`, and `learn_resolve`. They are crude instances of one
missing primitive — a **continuation-passing consent state machine**. The same primitive was the
blocker for destructive actions (an LLM must never run `empty_trash` unconfirmed) and for a GUI
[Approve]/[Deny] button.

Two ratified non-negotiables:
- **Never block the select loop.** The daemon is single-threaded (`server.py`); blocking it on human
  socket I/O would freeze ticks, the Sentinel, events, and every client. Suspension must be a heap
  record, not a blocked call stack.
- **Opaque-ID security boundary.** The executable payload (a validated catalog/executor proposal)
  stays server-side; the client receives only an opaque id and votes approve/deny. A buggy or
  compromised client can only vote on the daemon's *pre-validated* intent — consent-channel command
  injection is impossible.

## Decision
A new `consent` domain + a `ConsentService` shell own every binary consent flow.

- **`src/consent/` (pure):** `ConsentRequest` (frozen: id, kind, prompt, label, created_at,
  expires_at, `expiry_default`, status) carries **no executable** — only `to_public()` wire data.
  `ConsentLedger` holds the open requests; `resolve`/`expire_due` are one-shot pops (the basis for
  idempotent resolve and impossible double-execute). Fully unit-tested.
- **`src/service/consent_service.py` (shell):** wraps the ledger + the **continuations**
  (`on_approve`/`on_negative` thunks, held here keyed by id, never serialized). `request(...)` mints
  an id + emits `consent_request`; `resolve(id, accepted)` runs the matching thunk once + emits
  `consent_closed`; `sweep(now)` reaps overdue requests applying their `expiry_default` + emits
  `consent_closed reason=expired`; `snapshot()` is the `consent_sync` payload. Never blocks.
- **Protocol:** one command `consent_resolve {id, accepted}`; events `consent_request`
  `{id,kind,prompt,label,expires_at,server_now}`, `consent_closed {id,reason}`, `consent_sync
  {requests,server_now}`.
- **Wiring:** `Session` owns the service; `tick()` sweeps; `server._accept` **unicasts** a
  `consent_sync` to each (re)connecting client.
- **Producers migrated:** the **Sentinel ask-mode** ([sentinel_loop.py](src/service/sentinel_loop.py))
  opens a consent request (approve → hide + positive evidence; deny/expiry → negative evidence); the
  **executor `act`** ([session.py](src/service/session.py) `_act`) opens one whose continuation is
  `execute_approved(proposal)`. Both clients (Swift app + terminal console) resolve via the unified
  command.
- **Destructive actions:** `actions.Action` gains `confirm`; `empty_trash` is registered
  (`confirm=True`). `ActionRunner.propose` returns a `ConsentSpec` (label + on-approve thunk) instead
  of executing; `Jarvis.converse` routes it through an injected `consent_opener`.

### No-hung-card guarantee (the expiry-cleanup answer)
Three overlapping mechanisms make a permanently-stuck card structurally impossible:
1. **Client-side TTL** — the card arms a local timer at `expires_at − server_now` (a *duration*, skew-
   proof) and self-dismisses even fully offline.
2. **`consent_sync` on (re)connect** — the client reconciles its cards against the authoritative
   open-set: cards absent from the snapshot (already expired/dropped while away) are removed; missing
   ones rendered.
3. **`consent_closed`** — while connected, an expired/resolved request dismisses the card immediately.
Plus **idempotent pop-on-resolve**: a click racing server expiry hits a gone id → "no pending
request," never a phantom approve or double-execute.

## Consequences
- **Gained:** one tested consent primitive serving the Sentinel + the action layer; destructive
  actions now possible behind a real Approve/Deny; the daemon never blocks; reconnect-safe UI.
- **Tests:** +24 (`consent/test_ledger.py`, `service/test_consent_service.py`,
  `service/test_consent_sentinel.py`; extended `actions/test_run.py`, `test_converse.py`,
  `test_voice.py`). Consent tests are pure or stubbed — **no OS side effects**; `empty_trash` uses an
  injected fake spawn. `python3 -m pytest src` = **330**.
- **Honest limits / deferred:**
  - **`learn_resolve` NOT migrated** — it is multi-select (approve a subset of N escalated claims),
    not binary; folding it in = "one consent request per claim," a deliberate later step.
  - **Sentinel auto-mode/undo** stays on its `acted`/`intervene` path — it is post-hoc (the action
    already happened; a timeout means *keep*), a clean later fold-in with `expiry_default="approve"`.
  - **Swift layer has no pytest harness** (per ADR-017) — Phase 2 is a live checklist; `build.sh`
    compiles clean. The security-critical daemon core is fully unit-covered.
  - **`empty_trash` is genuinely destructive** — it is the single new destructive action, gated behind
    consent as the proof; further destructive ops are now one-line catalog additions.
  - **TTL default is 120 s** — tunable per request via `ttl`.

## Alternatives Considered
- **Block the loop waiting for the reply:** rejected — freezes the single-threaded daemon.
- **Send the executable to the client and run what it returns:** rejected — that is the injection
  hole the opaque-id boundary closes.
- **Big-bang migrate all three legacy flows at once:** partially rejected — migrated the two binary
  flows (Sentinel ask, executor act) + greenfield destructive actions; deferred the multi-select
  `learn_resolve` and post-hoc undo to keep the change correct and reviewable.
