# persona

## Overview
The ADR-036 **continuous persona-concept layer**: the assistant learns the user's stable working
*style* and *focus* (e.g. "prefers terse markdown tables", "currently doing local development") and
feeds it back into every LLM turn as a `[COGNITIVE CONTEXT CONSTRAINTS]` system-prompt prefix. Learning
runs on an **isolated, resilient ONA instance** fed by an **idle-gated, bounded batch** ingestion
pipeline (so the blocking 7B extraction never steals compute from a live turn); truths are checkpointed
to SQLite; and **injection reads SQLite** (an O(1) read, no ONA on the hot path). Distinct from the
Habit Brain (which proposes time/app *actions*) — persona is semantic/style only and **never gates an
action**. It only shapes the prompt; the closed catalog + consent ledger remain the action firewall.

## Usage
```python
from persona import PersonaStore, extract, render_persona
store = PersonaStore("jarvis.db")
store.buffer_event("just give me the bullet points, skip the intro")   # O(1) ingest
# (the service/persona_loop drains batches when idle: extract -> persona ONA -> upsert)
render_persona(store.current(0.75))   # -> "[COGNITIVE CONTEXT CONSTRAINTS]\n- Omit greetings ..."
```
The daemon owns the loop: `service/persona_loop.py` drives ingestion from `session.tick()` (idle-gated),
and `jarvis.converse()` prepends `render_persona(...)` to the system prompt.

## Key Components
- **`vocab.py`** (pure) — the **closed, developer-curated** vocabulary: the only `(predicate, value)`
  pairs the layer may learn/inject, their ONA terms, and their English constraint phrases. This is also
  the prompt-injection bound (untrusted scraped text can only nudge known dimensions). `render_persona`.
- **`extract.py`** (functional core) — the 7B emits **JSON** (never raw Narsese); code validates each
  item against `vocab` and renders the term. Malformed/out-of-vocab is dropped, so the NAR only ever
  receives clean statements. `generate` is injected (testable without a model).
- **`store.py`** — `PersonaStore`: the O(1) `persona_events_pending` buffer + the `persona_concepts`
  checkpoint `(term, frequency, confidence)` that doubles as the injection source and the replay source.

## Dependencies
`brain` (the isolated, resilient ONA instance; `BrainUnavailable` for fail-closed), the daemon's LLM
(injected `generate`), stdlib `sqlite3`. No network.

## Related ADRs
[ADR-036](../../docs/adrs/ADR-036-continuous-persona-learning.md) (this module),
[ADR-011](../../docs/adrs/ADR-011-sentinel-persistence.md) (the replay pattern),
[ADR-026](../../docs/adrs/ADR-026-habit-brain.md) (the distinct Habit Brain),
[ADR-007](../../docs/adrs/ADR-007-llm-first-brain.md) (the LLM-first stance this partially walks back).
