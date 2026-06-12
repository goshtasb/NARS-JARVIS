# ADR-051: "Run Now" — steady-cadence tick + result visibility

## Status
Accepted. Suite 521 passed, 1 skipped. Live-verified end-to-end (see Validation). This is the
prototype slice; the formal **unified-Canvas UX ADR** remains deferred until this prototype is judged.

## Context
Two defects surfaced when the user added a document to the Batch Canvas, asked JARVIS to process it,
**and nothing visibly happened** — "where does JARVIS save the results?! where does the user see the
results. this was designed with extremely poor UI/UX."

Tracing it produced three distinct, compounding failures:

1. **The runner starved under load.** `server.serve()` only ticked the session on a *fully-idle*
   select timeout: `if not ready: self._session.tick()`. The overnight runner advances one task per
   `tick()`. So the moment *any* activity existed — the Canvas polling `overnight_status` every 1 s,
   a busy sensor pipe, an active chat — `select` returned `ready` before the 2 s timeout and `tick()`
   never fired. **Watching a run starved the very runner you were watching.** The overnight model
   assumed idle-only execution (it was built for overnight, ADR-031); "Run Now" runs during *active*
   use, which the loop never accommodated.

2. **Results were rendered nowhere.** Each task's output is written to `overnight_queue.result`
   (TEXT) and exposed by both `overnight_status` and `briefing`. But every UI that displayed a task
   dropped that column: `MorningBriefingView.doneRow` showed `• action arg`, and the Canvas status
   showed only a `✅ done` badge. The summary — the entire deliverable — lived only in a SQLite
   column, readable solely via `sqlite3 jarvis.db "SELECT result …"`. (The silent-fail-as-done bug
   itself was already fixed in v1.16.4; this is the *visibility* half.)

3. No control surfaced the difference between "run later/overnight" (Commit Queue) and "run now."

## Decision

### 1. Tick on a steady cadence, not only when idle (touches ADR-003)
`server.serve()` now handles any ready fds **and then** ticks whenever `self._poll` seconds have
elapsed since the last tick, tracked by `self._last_tick = time.monotonic()` — regardless of activity:

```python
now = time.monotonic()
if now - self._last_tick >= self._poll:
    self._session.tick()
    self._last_tick = now
```

This restores the ADR-003 single-threaded contract's intent: background loops (overnight runner,
sensor poll, habit/consent sweeps) advance on a bounded cadence **even under continuous client
activity** — they no longer depend on the daemon being idle. Cost is one `tick()` per `_poll` (2 s)
window under load; `tick()`'s work is already bounded (psutil read + one task + idle-gated loops).

**Honest limitation (deferred, not solved here):** the runner still executes each task
*synchronously inside `tick()`*. Light/subprocess tasks (`report_system`, web egress via the
isolated `web.py` subprocess, file reads) advance without meaningfully blocking. A **heavy** LLM task
(`summarize_file` over a large PDF = many sequential 7B calls) will still block the select loop for
its full duration — chat and sensing pause until it finishes. Making heavy LLM work truly
non-blocking (chunked across ticks or offloaded to a subprocess) is the "async engine under load"
piece explicitly deferred from the ADR-049/050 arc. Run Now is now *correct and visible*; it is not
yet *non-blocking for heavy work*.

### 2. Render the result where it was produced
Both surfaces now show the actual output, **selectable** (copyable):
- **Canvas** (`renderRunStatus`): a bordered `resultBox` under each done/failed row holds the full
  result text.
- **Morning Briefing** (`doneRow`): the result is rendered beneath the task line in a vertical stack.

The deliverable is no longer buried in the DB or a separate window.

### 3. "▶ Run Now" affordance
The Canvas start button is relabeled **▶ Run Now** (blue bezel + tooltip), distinct from committing
the queue for later. `send(start:)` kicks the runner and starts `startStatusPolling()` — a 1 s timer
that re-renders `overnight_status` and stops after 2 idle ticks. (No new "run now" *flag* was needed:
with the cadence fix, the existing immediate-start path now actually advances; the gap was cadence +
visibility, not a missing trigger.)

## Validation (live, this machine)
With the daemon restarted on the fix, a batch `[report_system, read_article("/tmp/mydoc.pdf")]` was
started and polled every 1 s (the UI's cadence):

```
t0s  active=True   report_system=pending   read_article=pending
t2s  active=True   report_system=done      read_article=pending
t4s  active=True   report_system=done      read_article=failed
t6s  active=False  report_system=done      read_article=failed
[done]   report_system: "System report:\n- CPU: 17%\n- Memory: 70% used …"
[failed] read_article:  "[ERROR: \"/tmp/mydoc.pdf\" is a local file or non-URL …]"
```

Before the cadence fix, the same poll-every-1 s loop held both tasks at `pending` indefinitely
(activity starved `tick()`). After: they progress to `done`/`failed` and their result text is
captured and rendered. Test rows were deleted from the live DB afterward.

## Consequences
- "Run Now" works under active use and is observable; results are readable in place.
- All tick-driven background work is now cadence-bounded under load, not idle-gated — a system-wide
  robustness gain beyond the Canvas.
- Heavy synchronous LLM tasks can still block the loop; the unified-Canvas ADR must address async
  execution before Run Now is advertised for large document work.
