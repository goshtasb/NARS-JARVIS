# ADR-026: The Habit Brain (Phase 1 — temporal habits)

## Status
Accepted & live-verified. **The core vision, finally built:** the execution layer now writes evidence
back to NARS, so recurring actions become habits JARVIS proposes itself. Suite 371 → **386**. Tagged
**v1.2.0**.

## Context
Honest status (ADR-025 follow-up) found the gap: the whole action stack (ADR-019→025) was LLM-driven and
**never fed NARS** — ONA was a memory bucket + the narrow Flow-Sentinel gate. "JARVIS learns your
habits" was unbuilt. ADR-026 closes the loop: action → Narsese evidence → autonomy gate → self-proposal.

Phase 1 deliberately uses **one coarse temporal dimension** (hour-bucket × action[+arg]) to prove
convergence before adding context. Mechanical reason (the whole thesis): ONA confidence is `c=w/(w+1)`;
only a *recurring* term accumulates `w` past 0.85. Quantized buckets make "9am today/tomorrow" the same
term; a dense/raw context would make every event a singleton (`w=1, c=0.5, E=0.75`) and never converge.

## Decision
- **`habits/quantize.py` (pure):** `time_bucket` (hour), `habit_key`/`habit_term` (term-safe),
  `habit_evidence` (asymmetric `YES {1.0 0.5}` / `NO {0.0 0.9}` — the Sentinel's *verified* ramp, not
  the eager `{1.0 0.9}`), `eligible` (only `argv`/`nav`, non-`confirm`, non-read-only — searches and
  destructive ops never become habits).
- **`habits/store.py` `HabitStore`:** durable mirror of each habit's truth + `(bucket, action, arg,
  last_proposed)`; replayed into ONA on start (ADR-011, since ONA has no save).
- **`service/habit_loop.py` `HabitLoop`:**
  - `observe(action, arg, outcome)` — the telemetry seam: feeds the knowledge brain `habit_evidence`
    (`did`/`approved` → YES, `denied` → NO) and write-throughs the new truth. Called from
    `jarvis._run_actions` after every eligible actuation (user-asked → positive evidence) and from the
    proposal's consent continuations.
  - `propose_due(now)` — from `session.tick`: for the current bucket, any habit whose
    `gate_passes(freq, conf)` and not yet proposed this occurrence (cooldown) → opens an **ADR-020
    consent** ("You usually `<x>` around this time — want me to now?"). **Never auto-acts;** approve
    actuates + reinforces, deny collapses.
- Reuses `service.autonomy.gate_passes/expectation/floors` verbatim — the gate is identical to the Sentinel's.

## Consequences
- **Gained:** JARVIS forms temporal habits from your behavior and proposes them — agent, not tool.
- **Live-verified** (real ONA + store + consent): the day-by-day ramp `0.50→0.857` **arms at 6
  confirmations**, the proposal fires through the consent gate, and `find_file` (read-only) never forms
  a habit.
- **Tests:** +15 — `habits/test_quantize` (4), `habits/test_convergence` (4, **real Brain**: ramp arms,
  one denial collapses, **dense context never converges**), `service/test_habit_loop` (5, stubbed:
  feed/persist, eligibility, gated proposal + cooldown), `test_converse` (2, the telemetry hook). Suite **386**.
- **Safety:** it only ever *proposes* — the consent gate stays the single actuation checkpoint, so a
  mislearned habit costs at most one "Deny" (which also collapses it). No unsupervised action.
- **Honest limits:**
  - **Temporal-only (Phase 1).** Real habits often need weekday/foreground context → Phase 2 (add ONE
    coarse, quantized dimension at a time so terms still recur).
  - **Needs real repetition** (~6 same-hour occurrences ≈ about a week). The clock is injectable for
    tests; in real use a habit forms over days.
  - **Knowledge-brain reuse** (namespaced `<habit_… --> [approved]>` terms, disjoint from conversational
    facts). A dedicated habit ONA (Sentinel-style isolation) is a clean later refinement.
  - **Eligibility is conservative** — safe reversible state-changers only.

## Alternatives Considered
- **Dense context vector** (`=/> [9am, app, cpu, mem]`): rejected — every event a singleton, `w` frozen
  at 1, never crosses the gate. Proven by `test_dense_context_never_converges`.
- **`{1.0 0.9}` approval weight:** rejected — arms after ONE occurrence (`E=0.95`). `{1.0 0.5}` forces
  the multi-confirmation ramp.
- **Auto-act on an armed habit:** rejected — it *proposes* through consent; the human is the trigger
  (and reinforces). Trust earned slowly, lost on one deny.
