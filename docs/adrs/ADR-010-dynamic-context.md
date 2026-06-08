# ADR-010: Dynamic context (live facts) + volatile-memory guard

## Status
Accepted — fixes the "timeless void" bug and is a first step toward ADR-007 open item (c) "injecting
sentinel habits/state". Builds on ADR-008/009 auto-memory.

## Context
`Jarvis.converse` built its prompt from persistent memory + the question only — JARVIS had **no
clock and no system awareness**, so "what time is it?" was guessed. Worse, when the user corrected it
("it's 8pm"), the auto-memory pipeline saved `"the current time is 8 pm"` as a **durable** memory and
recall then reported 8pm forever (we found exactly these rows in the live DB). Transient facts were
being frozen as permanent ground truth.

## Decision
Two coupled changes, both deterministic.

**1. Inject live context every turn (not LLM tool-calling).** A new `context` domain renders a small
block from fresh inputs and `converse` injects it above the persistent-memory block, labelled
*"Current context (live — answer from this; do NOT memorize it)"*. v1 providers:
- **date/time** — always (`clock_fact`, host local tz via `datetime.now().astimezone()`).
- **system** — the psutil snapshot the daemon already keeps (`session._last`, e.g. `cpu=12% mem=63%`).
- **foreground** — the Flow Sentinel's coarse app **category** + attention (`SentinelLoop.current_context()`),
  included only when the sentinel is ON; omitted otherwise so JARVIS never invents activity.

Chosen over LLM tool-calling because the local 7B's "halt → emit tool call → resume" reliability is
poor (the recurring ADR-008/009 lesson); injection is O(1) latency and *guarantees* the data is
present. Tool-calling is noted as possible future work.

**2. Volatile-fact guard (`context.is_volatile`).** A pure, closed pattern set (current time/date,
"it's 8pm", "today is…", "right now", live cpu/mem) checked in `Jarvis._remember_facts` — volatile
statements are **never persisted**. The prompt also says not to memorize live facts, and the live
lines are added to the echo-guard `known` set, but `is_volatile` is the hard backstop (never rely on
the 7B). Posture is **default-ALLOW**: only clearly-ephemeral text matches; a missed novel phrasing
fails safe to *saving* (visible `(Saved:)`, user can `forget`) — silently dropping a legitimate
memory is the worse error.

## Architecture
- `src/context/providers.py` (pure given inputs): `clock_fact`/`system_fact`/`foreground_fact`/
  `render_live_context`. `src/context/volatile.py` (pure): `is_volatile`. Public via `__init__`.
- `Jarvis` gains `context_provider: Callable[[], str] | None` (dependency-injected) — the impure
  clock/psutil/sentinel reads live in the **imperative shell** (`Session._live_context`), so the
  reasoning core stays pure and unit-testable. None → no live context (tests/offline).
- `SentinelLoop.current_context() -> (category|None, attention|None)` — gated on `running()`.

## Consequences
- **Gained:** correct time/date/system answers from fresh context; "what am I working on?" answers
  by category when the sentinel is on; transient facts no longer pollute durable memory.
- **Bridge:** sentinel **state** flows read-only into the knowledge brain's context (never merged
  into memory) — dual-brain isolation holds; a concrete down-payment on ADR-007 (c).
- **Accepted limitations:** foreground is **coarse category only** (dev/comms/media…), never app
  name or window content (TCC-free design), and only when the sentinel is enabled (off by default).
  The volatile pattern set is finite (fails safe to saving). Timezone = host local, no config in v1.
  Tool-calling deferred.

## Alternatives Considered
- **LLM tool-calling for time/system:** rejected for v1 — unreliable on the local 7B, higher latency.
- **Compute the clock inside `Jarvis`:** rejected — impure; injected via `context_provider` keeps the
  core testable.
- **Default-deny volatile guard:** rejected — risks dropping legitimate memories; default-allow + the
  visible/forgettable save is safer for a storage engine.
