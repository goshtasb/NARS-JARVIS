"""habits — the Habit Brain (ADR-026): turn actions into NARS evidence so JARVIS learns recurring
habits and proposes them through the consent gate.

Pure core (`quantize`: context→Narsese mapping + eligibility) + durable store (`store.HabitStore`).
The stateful loop (telemetry hook, replay, gated proposal) lives in `service.habit_loop`. The gate math
is reused verbatim from `service.autonomy` (the Sentinel's verified ramp).

Public interface (ADR-001).
"""
from .quantize import (
    NO,
    YES,
    bucket_label,
    describe_habit,
    eligible,
    evidence_count,
    habit_evidence,
    habit_key,
    habit_term,
    time_bucket,
)
from .store import HabitStore

__all__ = [
    "time_bucket",
    "habit_key",
    "habit_term",
    "habit_evidence",
    "eligible",
    "bucket_label",
    "evidence_count",
    "describe_habit",
    "YES",
    "NO",
    "HabitStore",
]
