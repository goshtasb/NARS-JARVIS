# ADR-052: Document-summary offload engine (heavy LLM work off the select loop)

## Status
Accepted. Suite 521 → **527** (+6). Live-verified end-to-end with the real 7B (see Validation). This
is the prerequisite Synapse named: the heavy-task non-blocking foundation that ADR-051 deferred, and
the gate the unified-Canvas UX must clear before it can advertise large-document processing.

## Context
ADR-051 made the overnight runner advance under load and render its results, but left one honest flaw:
the runner executes each task **synchronously inside `session.tick()`**. Light tasks (reports, file
reads, subprocess web egress) are fine. But `summarize_file` is a Map-Reduce over many sequential 7B
calls — **minutes** of in-process inference ([llm.py:50](../../src/language/llm.py) is a blocking
native call). On the single-threaded select loop, that freezes everything: chat, the consent sweep,
and — the decisive objection — the Sentinel's `usage_events` ingestion. The Passive Observation Mirror
(ADR-050) would go **blind for the entire summary**, corrupting the very data-sufficiency experiment
it is running. A Canvas that hangs the neuro-symbolic engine every time it "thinks" is not shippable.

## Decision
Offload heavy document summarization to a **detached CPU worker** whose stdout the daemon's `select()`
multiplexes — the exact construct already proven for voice transcription
([`WhisperJob`](../../src/service/voice.py)). Three ratified parameters, each defending a known trap:

### 1. Physical isolation — a fresh subprocess, not threads/asyncio/fork
[`summary_worker.py`](../../src/service/summary_worker.py) runs as its own interpreter
(`python -m service.summary_worker <file> <task_id>`), spawned via the sanctioned `safespawn.popen`.
Threads/asyncio are disqualified: even though llama.cpp releases the GIL during inference, the
single-threaded loop never returns to `select()` while that call is on the stack, so it stays frozen.
`fork()` is disqualified: the daemon has already initialized a Metal/GPU context and the ~1.15 GB ONA
bank — neither survives fork. A fresh interpreter sidesteps both. The worker loads **only** the LLM
(no ONA), and the GGUF weights are mmap-shared with the daemon via the OS page cache, so its model
load adds no second copy of the weights to RAM.

### 2. CPU-only — protect the interactive Metal context
The worker sets `NARS_JARVIS_GPU_LAYERS=0` **before** importing the model, so it never contends for
the GPU the foreground `converse` needs for latency. Cost: a CPU summary is slower and shares CPU
cores with the daemon (it is non-blocking, not free — see the latency note in Validation).

### 3. DB-silent worker — SQLITE_BUSY eliminated by construction
The worker writes **nothing** to `jarvis.db`. It streams a line protocol to stdout; the **daemon**
performs the single `overnight_queue` write when it reads each record (the WhisperJob contract). One
writer touches the queue, so a cross-process write collision cannot arise from the worker.

**Line protocol** (one record per line, flushed): `[progress] {"i":k,"n":N}` before each map chunk,
then exactly one terminal `[result] "<summary>"` or `[error] "<msg>"`.
[`SummaryJob`](../../src/service/summary_job.py) mirrors `WhisperJob`: its stdout `fileno()` is
returned from the runner's `extra_fds()`, the daemon's `select()` wakes on it, and `handle_fd` drains
with `os.read` (never blocks — only what `select` flagged), parses each line, marks the queue
(`running` with live `summarizing… chunk i/N`, then `done`/`failed`), and emits `overnight_progress`
to the socket. The Canvas renders that live string in the result panel (ADR-051).

The runner ([overnight_runner.py](../../src/service/overnight_runner.py)) offloads only
`_OFFLOAD = {"summarize_file"}`; while a job is in flight `advance()` is a no-op, so one heavy job
runs at a time and the queue resumes draining on the worker's EOF. A crash mid-job leaves the row
`running`; `reset_running()` reverts it to `pending` on next start (existing ADR-031 safety).

### 4. WAL — the non-negotiable that precedes the engine
Audit found **no** `journal_mode=WAL` and **no** `busy_timeout` across the daemon's 8
`sqlite3.connect()` sites — default rollback journal (whole-DB write lock) with only Python's implicit
5 s timeout. The Sentinel's per-app-switch `usage_events` write already serialized against queue/memory
reads. New [`dbconn.connect()`](../../src/dbconn.py) sets `journal_mode=WAL` + `busy_timeout=5000` and
is now the single bootstrap for all stores (memory, habits, sentinel, persona, overnight, grounding,
metrics). WAL lets the Sentinel writer and all readers proceed concurrently. (`:memory:` test DBs
ignore WAL harmlessly.)

## Validation (live, this machine, real 7B)
Foreground converse stayed alive ("Two plus two is four"). A real `summarize_file` on a ~9 KB doc was
Run-Now'd:
```
t 0s pending   wk=-       rt=0.1ms
t 4s running   wk=51612   rt=3.9ms   summarizing… chunk 1/2
t32s running   wk=51612   rt=0.5ms   summarizing… chunk 2/2
t45s done      wk=-       rt=0.4ms   Summarized jarvis_test_doc.txt: …
worker ran as a separate process: True
socket round-trip DURING the summary: avg 25.6 ms (one 1.1 s CPU-contention spike), vs ~45,000 ms if blocked
```
The worker is a distinct PID; live progress streamed; the daemon answered every poll in ms while a
45 s CPU summary ran; a coherent summary was produced. **Honest latency note:** the lone ~1.1 s spike
is real — a CPU-bound 7B contends for cores, so the loop is *non-blocking* but not *isolated from CPU
pressure*. Acceptable for background batch work; it is why the worker is CPU-capped rather than also
GPU-bound. Six new tests cover the WAL pragma, the progress hook, the protocol parser, the runner
offload control-flow, and the worker end-to-end (fake LLM). Test row deleted from the live DB after.

## Consequences
- Heavy document summaries no longer block the loop or blind the Sentinel — the mirror experiment is
  safe. Run Now (and the future Schedule tab) inherit the offload automatically via the runner.
- The daemon is now WAL across all stores — a system-wide durability gain beyond this feature.
- Only `summarize_file` is offloaded today; inline web egress (`read_article`, bounded 45 s) remains a
  smaller, separate blocking source and can join `_OFFLOAD` later if it proves disruptive.
- The unified-Canvas UX (tabbed real-time / scheduled) is now unblocked to build on this foundation.
