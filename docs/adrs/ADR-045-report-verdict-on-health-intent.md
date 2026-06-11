# ADR-045: System-report "all clear" verdict only on health intent

## Status
Accepted & live-verified. Refines ADR-040's scope-honest verdict. Suite 496 → **498**.

## Context
User asked, by voice, *"which one of these applications is using the most memory?"* JARVIS answered
correctly ("Python") **and then appended a full system report ending "Nothing looks wrong in these
metrics."** The user's objection, verbatim: *"I didn't ask if something was wrong. I asked which one
of these applications is using the most memory."*

Two distinct things were conflated. `report_system` legitimately fired — the question is about memory,
so `_is_system_query` matched and the report is the *data source* for the answer (top-memory line).
That part is correct. But `report_system` always tacks on an "all clear" **health verdict**, and that
verdict is unsolicited noise on a neutral data question — it answers a question ("is anything wrong?")
the user didn't ask.

## Decision
Split "system data question" from "system **health** question" and gate only the reassurance verdict:
- A new narrower `_HEALTH_QUERY` (`_is_health_query`) matches actual health phrasings — wrong / slow /
  hot / ok / broken / crash / "system report|status|health|check". It is a *subset* of the existing
  `_SYSTEM_QUERY` that still gates whether `report_system` runs at all.
- `diagnostics.NOMINAL_VERDICT` is pulled out as a constant; `drop_nominal_verdict(report)` removes
  **only** that "all clear" line (pure, selective, idempotent, a no-op on non-report strings).
- In `_run_actions`, a `report_system` result for a non-health question has the nominal verdict
  stripped. A real `Anomalies: ⚠…` line is **never** dropped — a surfaced problem is never unsolicited.

So: "which app uses the most memory" → metrics, **no** "nothing looks wrong" editorial. "Is anything
wrong with my Mac?" → metrics **with** the verdict. Implemented as a post-process over the existing
propose path, so the action-runner / consent signatures are untouched and the stub runners in the
test suite keep working.

## Consequences
- **Gained:** neutral system-data questions get the data without a health editorial the user didn't
  ask for; health questions are unchanged; anomalies always surface.
- **Honest limitation (not fixed here):** `report_system` is still a *full dump* — a narrow memory
  question gets CPU/disk/battery lines too, because the action has no per-question focusing. A truly
  targeted answer ("Python is using the most memory, 29%") would need a report-synthesis second pass
  (the ADR-039 web-research pattern applied to diagnostics). That is a real feature with a latency
  cost (an extra 7B call) — deliberately deferred to a considered decision, not rushed in. This ADR
  fixes exactly the stated complaint (the unsolicited verdict) and no more.

## Alternatives Considered
- **Thread a `health_intent` flag through `ActionRunner.perform`/`perform`/`system_report`** — rejected:
  it changes the runner's question-agnostic signature and breaks the duck-typed stub runners across the
  test suite; the post-process over the existing path is non-invasive and equally correct.
- **Drop the verdict from `system_report` by default** — rejected: overnight/other callers and the
  ADR-040 tests rely on the scope-honest verdict; default behavior stays, the conversational layer
  opts out per question.
- **Tighten `_SYSTEM_QUERY` so the memory question doesn't fire `report_system`** — rejected: then the
  answer ("Python") would be an *ungrounded guess*; the report is what grounds it. The fix is to drop
  the editorial, not the data.
- **Full report-synthesis answer now** — deferred (see Consequences): the right bigger fix, but a new
  model call near a freeze; flagged for the review instead of rushed.
