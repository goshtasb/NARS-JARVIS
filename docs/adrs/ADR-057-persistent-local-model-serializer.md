# ADR-057: The Persistent Local-Model Serializer (Tier-2 Off-Load)

## Status
**Accepted** — landed and headless-tested. Live-verified against the real 7B (see Measurements).

## Context
The 3-tier on-device cascade (ADR-056/Gate 2) is: Tier 1 deterministic vault (`recall`) → on abstain →
Tier 2 the private local 7B (`converse`) → on manual toggle → Tier 3 cloud. Tiers 1 and 3 already run
**off** the daemon's single `select()` loop (the recall worker subprocess; the `CloudJob` thread). Tier 2
did **not**: `converse` called `LocalLLM.generate_text(..., max_tokens=512)` **synchronously on the loop
thread**, so a general/coding question that fell through to the local model locked the loop for the full
decode (~8 s observed) — a macOS beachball, and during that window the Sentinel's sensor fd is undrained.

The fix could not reuse the ephemeral-subprocess pattern (recall worker / `CloudJob`): a 4 GB+ GGUF takes
seconds to load, so spawning a worker per query trades the beachball for a load-bar. The model must be
**persistent**.

The hard constraint that shaped the design: there is **one** `LocalLLM` (one llama.cpp context), and it is
**non-reentrant** — but it has ~9 callers (converse, the GBNF intent router, NL→Narsese translation, the
persona idle-batch, the web-agent step, voice formatting, work-actions). Two of them fire from the periodic
`tick()` (`_drive_agent`, the persona batch). So the real requirement was not "thread the converse" — it was
**serialize every access to the context** while moving the long decode off the loop. (The sentinel narrator
is wired to `NoNarrationLLM`, not the model, so it is not a concurrent caller — removing the scariest
collision, a converse-CPU-spike → surprise → narrate race.)

## Decision
`service/local_brain.py` — **`LocalBrain`**, a single-owner serializer wrapping the one Multiplexer,
injected everywhere the model was injected (so every existing caller is unchanged, now lock-guarded):

1. **One lock, all access.** Every inference method (`generate*`, `to_claims`, …) takes one `threading.Lock`
   before entering the context. `__getattr__` delegates so `hasattr(brain, "generate_text")` still reflects
   the *real* model's capabilities (a no-GGUF demo source has none, and that must keep surfacing).
2. **The long decode runs off-loop, on a background thread.** `submit(token, system, user, max_tokens)`
   decodes on a worker thread (llama.cpp releases the GIL during the C decode, so the loop keeps iterating)
   and signals completion on a **self-pipe fd the `select()` loop already polls** — same mechanism as the
   cloud/recall workers. The main loop never blocks on token generation.
3. **`converse` split into three stages** (`jarvis.py`): `converse_begin` (prompt assembly — ONA recall +
   SQLite context, **main thread**) → off-loop decode → `converse_resume` (post-processing: `[[DO:]]`
   actions, `[[REMEMBER]]`/`[[FORGET]]`, output grounding — **main thread**). Only the pure token-decode
   leaves the loop; ONA and SQLite (single-owner / thread-affine) are never touched off-thread.
4. **Periodic model-callers gated on `busy`.** While a decode is in flight, `tick()` skips `_drive_agent`
   and the persona idle-batch, so they never block on the lock. The synchronous user paths (intent router,
   translation) stay synchronous under the lock — correct under overlap, and overlap is rare since the user
   is waiting on the answer.

Wire-up (`session.py`): `_begin_converse` returns a fast `{"status":"thinking_local","token"}` ack; the
answer is emitted later as a **`local_answer`** event. Voice routes through the same path (`voice=True` →
spoken on the event). The UI (`ChatView.swift`) shows a "🧠 Thinking locally…" row on the ack and renders
the answer on the event. `offloop_in_flight()` includes `localbrain.busy` so the daemon's loop-gap meter
covers the decode window.

## Consequences
- **The Tier-2 beachball is gone.** Live, against the real 7B: ack in **28.7 ms**; **40 concurrent `status`
  requests round-tripped at max 0.4 ms / median 0.2 ms while the model was mid-decode** — the loop services
  traffic instantly while the 7B runs.
- Headless, under continuous flood (the realistic sensor-load proxy): daemon **`loop_max_gap_ms = 0.9 ms`**
  during the decode (poll = 200 ms). Test: `test_tier2_local_decode_runs_offloop_without_blocking_the_loop`.
- **Loop-gap meter caveat (honest):** `loop_max_gap_ms` measures wall-time *between loop iterations*, which
  **includes the intentional idle `select()` sleep** (production poll = 2.0 s). With no traffic to service,
  the meter reads up to ~2000 ms — that is the loop **sleeping efficiently, not a stall**. It only means
  "stall" when there is traffic that should have been serviced; the concurrent-RTT number (0.4 ms) is the
  unambiguous liveness proof. Under load (sensor telemetry / the flood test) the meter pins to single-digit ms.
- **Known follow-ups (not addressed here):** the rare research second-pass inside `converse_resume`
  (`[[DO: web_lookup]]` → synthesis) still decodes synchronously on the main thread — it runs only when the
  model itself requested a web lookup (not on a plain coding/general question), so it is out of scope for the
  reported P0; a future ADR can make it a second off-loop hop. If a user fires an intent-parse/`tell` *during*
  an active decode, that short call waits on the lock (correct, not a crash; rare).
