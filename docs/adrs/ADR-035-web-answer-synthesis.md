# ADR-035: Conversational web answer synthesis (readable results + a two-pass loop)

## Status
Accepted & live-verified. Fixes the v1.9.0 conversational web UX: raw JSON dumped into chat, and "Let me
check" with no actual answer. Suite 444 → **446**. Tagged **v1.9.1**.

## Context
ADR-034 shipped `web_lookup`/`read_article`, but they were built for the *overnight machine* path. Used
conversationally they failed two ways (real user repro on a "what time is sunrise tomorrow?" voice query):
1. `web_lookup` returned **raw JSON**, which `converse()` appended verbatim — the user saw
   `[ { "title": … } ]` flooding the chat (and TTS tried to read JSON aloud).
2. `converse()` is **single-pass**: the model says "Let me check," the search runs *after* its one call,
   so it never turns the results into an answer. The user got results (or filler) but never "sunrise is
   5:43 AM." (Worse, on other runs the 7B skipped searching and *guessed* a time it cannot know.)

## Decision
Two changes, scoped to make web search actually answer in conversation.

1. **Readable results (not JSON).** `actions/web.py` `parse_ddg` now returns a clean numbered
   `title / snippet / url` list. Better in chat AND better as input to the model than JSON; the overnight
   path stores/feeds the same readable text.

2. **Two-pass synthesis loop in `jarvis.converse()`.** Research actions (`web_lookup`, `read_article`) are
   split out from normal actions. After the first model call emits the `[[DO:]]`, the search runs, then a
   **second** model call (`_web_answer`, system prompt `_SYNTH_PROMPT`) answers the question USING ONLY the
   findings and naming the source — and that synthesized answer **replaces** the "Let me check" prose.
   Bounded: exactly one extra call (not a multi-round agent); findings capped at 8 000 chars to stay under
   `n_ctx`. If the search returns `[ERROR…]` (rate-limited/blocked), synthesis is skipped and the error is
   surfaced honestly — never a fabricated answer. If synthesis fails, it falls back to the readable findings.
   Memory directives are now parsed from the model's *original* prose before synthesis can replace it.

## Consequences
- **Gained:** "look up X online" now returns a real, source-cited answer instead of a code dump or empty
  "Let me check." Web results read cleanly and feed the model cleanly.
- **Live-verified:** a web question routes to `web_lookup` → results → a synthesized one-line answer citing
  the source; a forced `[ERROR]` surfaces the failure with no fabricated answer.
- **Tests:** +2 — synthesis returns the answer (not the raw list / not "Let me check"); an error result
  skips synthesis and surfaces honestly. Plus `parse_ddg` test updated to readable text. Suite **446**.
- **Cost:** a web-answering turn now makes **two** 7B calls (+ the fetch) — a few extra seconds. Acceptable
  for an actual answer; only web turns pay it.
- **Honest limits / not fixed here:** (a) the 7B still sometimes *guesses* instead of searching for
  time-sensitive facts (a separate decide-to-search behavior, hard to force on a 7B); (b) the over-eager
  auto-memory save seen in the repro ("user asked about an unexpected event") is a separate ADR-008
  issue; (c) single search round only — no follow-up "search again to refine."

## Alternatives Considered
- **Readable formatting only (no synthesis):** rejected by the user — it stops the code-dump but still
  shows a list of snippets, not an answer.
- **Full multi-round ReAct agent:** deferred — one search→answer pass covers the common case at bounded
  latency; iterative refinement is a later ADR if needed.
