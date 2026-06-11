# habits

## Overview
The Habit Brain (ADR-026): turns the actions you take through JARVIS into NARS evidence on
time-and-context patterns, so recurring habits can be learned and — once confidently armed — *offered*
through the consent gate. Pure core (quantization + eligibility) plus the durable store; the stateful
loop (telemetry hook, boot replay, gated proposal, forget) lives in `service/habit_loop.py`. The
cautious arming math is reused verbatim from `service/autonomy.py` (the Sentinel's verified ramp:
arm slowly, collapse on a single Deny).

## Usage
```python
from habits import HabitStore, context_key, habit_term, time_bucket, eligible

# context -> key -> the Narsese habit term fed to the isolated habit ONA
key = context_key(time_bucket(16), "mute", "", "weekday", "app_zoom")   # 'h16_mute_weekday_app_zoom'
term = habit_term(key)                                                  # '<habit_h16_… --> [approved]>'
# HabitStore checkpoints (term, truth) for replay + the 🧠 Habits dashboard
```

## Key Components
- `quantize.py` — context → Narsese mapping (hour bucket + action [+ weekday-type + foreground app],
  ADR-028) and the eligibility predicate (which catalog kinds may become habits at all).
- `store.py` — `HabitStore`: the write-through SQLite checkpoint (the ADR-011 replay pattern) feeding
  the 🧠 Habits dashboard (ADR-027/030).

## Dependencies
Standard library + SQLite. Evidence arrives only from actions routed through JARVIS (chat/voice
`[[DO:]]` or approved suggestions) — never from passive macOS usage.

## Related ADRs
ADR-026 (habit brain), ADR-027 (introspection/forget), ADR-028 (multi-variable context),
ADR-030 (dashboard), ADR-011 (write-through + replay).
