# ADR-042: Research loop hardening — deterministic floor, conversation context, trajectory log

## Status
Accepted & live-verified. Refines ADR-039 (whose architecture stands); the snippet trap is now
structurally impossible rather than prompt-dependent. Suite 489 → **492**.

## Context
Two field failures within hours of ADR-041 shipping, both caught by the user:
1. *"how is the weather tomorrow"* → "the findings do not provide specific details… check these
   sources." Live reproduction (temp 0, 3/3 trials): at the decision step the 7B replied
   `SEARCH how is the weather tomorrow` — re-issuing the SAME query instead of opening a link. The
   loop's `else: break` then treated the model's third refused choice as "research finished", so
   synthesis ran on snippets with **zero pages opened** — the exact trap ADR-039 was built to escape.
2. The follow-up *"are you sure?"* re-triggered `web_lookup`, and the research loop received the
   literal three words with no conversation context — it researched "are you sure?" against fresh
   weather results and honestly reported nonsense. (Notably the ADR-041 history DID work in the main
   prompt; the research path was history-blind and its answer replaces the conversational reply.)
3. Neither failure was diagnosable from the daemon log: the loop recorded nothing about its decisions.

## Decision
1. **Deterministic floor (code, not prompt):** if the model tries to end research — ANSWER, an
   invalid pick, or a duplicate search — while **zero pages have been read and the menu is
   non-empty**, the loop force-opens the top result and continues. ANSWER is honored once at least
   one page has been read. The injection bound is unchanged: the floor opens a *menu* link (code-
   extracted, SSRF-guarded); nothing the model names is ever fetched. Wall-clock/step/cap exits stay
   hard — bounded beats complete.
2. **Duplicate-search guard:** queries are tracked (normalized); re-issuing one is a refusal that
   triggers the floor, not progress that burns the search budget.
3. **Few-shot anchoring** in the decision prompt (the ADR-036/v1.11.1 playbook): "result snippets are
   NOT data — OPEN the most relevant link", with three worked examples. Measured live on the failing
   scenario: `SEARCH <same query>` 3/3 before → `OPEN 2` (the correct AccuWeather-tomorrow link) 3/3
   after. The floor guarantees correctness; the prompt restores decision quality.
4. **Conversation-aware research:** `run_research(..., context=)` carries the rendered ADR-041
   RECENT CONVERSATION block into the decide and synthesis prompts, so "are you sure?" researches
   the claim it refers to instead of the literal words. Plus a prompt rule: questions about JARVIS's
   previous answer are answered from the conversation — `web_lookup` only when genuinely new
   information is needed.
5. **Trajectory logging:** the loop emits one line per step (`search → N links`, `open (floor) URL →
   N chars`, `stop (verb) after N opens / M searches`) through an injected `log`, wired to stderr →
   the daemon log. The next field failure is a grep, not a forensic reconstruction.

## Consequences
- **Gained:** the snippet trap cannot recur (floor), follow-ups research their referent (context),
  failures are observable (log). The decision prompt grew ~120 tokens.
- **Honest correction to the record:** ADR-039's "live-verified" weather test predates this logging;
  with no trajectory recorded, it cannot be retroactively proven that a page was opened on that run.
  From this version forward the log makes such claims verifiable.
- **Risk accepted:** the floor can force-open a poorly-ranked top result (one bounded read of an
  already-SSRF-guarded menu link); the few-shot prompt makes that the rare path, and trial 3/3 shows
  the model now picks the *most relevant* link, not the first.

## Alternatives Considered
- **Prompt-only fix (no floor)** — rejected: v1.8.2 and this very incident both prove 7B protocol
  adherence cannot be a correctness dependency. The prompt proposes; code disposes.
- **Lenient decision parsing** (accept "open the first link" prose) — rejected: loosening the grammar
  invites misfires on hostile page text; the floor solves the giving-up case without weakening the
  parser.
- **Gating web_lookup on follow-up turns deterministically** — rejected: no reliable regex separates
  "are you sure?" from a genuine new research request; conversation context + the prompt rule handle
  it, and the floor bounds the damage if research runs anyway.
