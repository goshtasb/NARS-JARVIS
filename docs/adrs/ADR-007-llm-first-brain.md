# ADR-007: LLM-first brain — ONA demoted from gatekeeper to memory

## Status
Accepted — **supersedes the "two-brain hallucination check" thesis** that governed earlier phases
(the LLM-as-constrained-translator design in CLAUDE.md and the grounded `converse` path).

## Context
The prior architecture made the LLM a constrained translator with **zero authority to answer from
its own knowledge** — every query routed through ONA, which only knows what the user explicitly
taught it. Working as designed, this produced an assistant that (a) had no answers unless fed, and
(b) could only do toy syllogisms ("tim → bird → nice"). The user judged this useless as a daily
assistant and chose, explicitly, to trade the absolute no-hallucination guarantee for actual utility.

## Decision
**Invert the relationship.** The LLM is now the primary brain: it answers general questions, writes
code, and reasons from its own weights. **ONA is demoted to a persistent memory engine** — it holds
what the user has taught and (later) their habits, and those facts are **injected into the LLM's
context as ground truth** on each turn.

- `Jarvis.converse` is now LLM-first: build a prompt = `ASSISTANT_SYSTEM_PROMPT` + the user's
  persistent memory (English facts from L2 via `_recall`) + the question → `llm.generate_text`.
- The system prompt is inverted: *"You are JARVIS… answer using your own knowledge; treat the
  provided memory as absolute ground truth."*
- The legacy hallucination-proof path is preserved as `_converse_grounded` and used as a fallback
  when no model is wired (tests / offline) — so old guarantees still hold where there's no LLM.
- **Model upgraded** to Qwen2.5-7B-Instruct (Q4_K_M, ~4.7 GB, offline) — a 3B model babbles when
  unleashed on free-form queries; 7–8B is the local sweet spot. `run.sh`/`run-ui.sh` prefer the 7B,
  fall back to the 3B.

Verified end-to-end on the real 7B: "capital of France?" → "Paris"; "what is my name?" → the taught
value (memory injection); a code request → correct code.

## Consequences
- **Gained:** a genuinely useful assistant that knows things, reasons, codes, and **learns** the
  user's facts/preferences across sessions via memory injection. The whole Phase 1–4 chassis
  (daemon, IPC, menu-bar, voice, sentinel, autonomy, kill switch) is unchanged — only the cognitive
  loop flipped.
- **Lost / accepted:** the hard mathematical no-hallucination guarantee. The LLM can now be wrong.
  This was the project's original intellectual core; it is deliberately set aside for utility, with
  the grounded path retained as a fallback and a possible future "hybrid grounding" mode.
- **Open / next:** v1 learns by explicit `learn` + injection of all recent taught facts. Future work:
  (a) ~~retrieval (embedding-ranked memory) instead of dumping recent facts~~ — **done, see
  [ADR-009](ADR-009-memory-at-scale.md)**; (b) ~~auto-extracting memorable facts from conversation~~ —
  **done, see [ADR-008](ADR-008-auto-memory-extraction.md)**; (c) injecting sentinel habits —
  *partial: sentinel **state** (foreground category) injected as live context
  ([ADR-010](ADR-010-dynamic-context.md)), and sentinel beliefs now **persist** across restarts
  ([ADR-011](ADR-011-sentinel-persistence.md)); injecting learned habits into the LLM still pending*;
  (d) optional hybrid grounding
  where ONA flags contradictions against the LLM's answer.

## Alternatives Considered
- **Keep symbolic-only:** rejected by the user — not useful as an assistant.
- **Hybrid grounding (LLM answers, ONA grounds/flags):** deferred — more value-preserving but more
  work; recorded as the likely next step.
- **Stay on Qwen-3B:** rejected — too weak for free-form answers; would babble.
