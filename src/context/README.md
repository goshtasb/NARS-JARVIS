# context

## Overview
Everything that gets *injected around* the user's question at converse time, plus the guards that keep
transient context out of durable memory. Pure / functional-core throughout: these modules render and
validate text blocks; they perform no I/O of their own (callers supply readings).

## Usage
```python
from context import render_live_context, render_habits, ConversationBuffer, is_volatile
```
`jarvis.converse()` assembles: live context → habits → AX controls → persistent memory → recent
conversation (ADR-041) → the question. Each provider returns `''` to mean "omit my block".

## Key Components
- `providers.py` — the live-context block: clock/system/foreground facts (ADR-010, ADR-028).
- `habits.py` — learned-preference rendering + the habit-conflict control plane (ADR-012/013).
- `grounding.py` — output grounding: suppress answers that contradict held self-facts (ADR-014).
- `volatile.py` — the volatile-fact guard keeping "it's 8pm" out of durable memory (ADR-010).
- `history.py` — the sliding conversational window (ADR-041): in-memory, 3 exchanges, 15-minute
  session gap, render-only. The short-term memory that makes follow-up questions work.

## Dependencies
Standard library only. Persona rendering deliberately lives in `persona/` (it owns the vocabulary);
this package never imports other domain modules.

## Related ADRs
ADR-010 (live context & volatile guard), ADR-012/013 (habit injection/conflict), ADR-014 (output
grounding), ADR-028 (foreground category), ADR-041 (conversational history).
