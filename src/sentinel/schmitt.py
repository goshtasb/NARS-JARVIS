"""Schmitt-trigger discretizer — pure Functional Core (S-02). Hysteresis + dwell + edge-trigger.

Maps a continuous signal (CPU%, Mem%) to a small symbolic vocabulary, emitting a level name
ONLY on a confirmed transition. Asymmetric enter/exit thresholds (a deadband) make boundary
flapping mathematically incapable of re-triggering; a K-poll dwell rejects single-sample spikes.
Pure: (state, sample) -> (state, emit?). No I/O, no clock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ladder:
    name: str  # signal name, e.g. "cpu"
    levels: tuple[str, ...]  # low -> high, e.g. ("idle", "normal", "high", "pegged")
    rising: tuple[float, ...]  # rising[i]: enter levels[i+1];  len == len(levels) - 1
    falling: tuple[float, ...]  # falling[i]: drop levels[i+1] -> levels[i]; len == len(levels) - 1


# Initial constants (tunable in config — the mechanism is exact, the values are dials).
CPU_LADDER = Ladder("cpu", ("idle", "normal", "high", "pegged"), (15.0, 55.0, 88.0), (7.0, 47.0, 80.0))
MEM_LADDER = Ladder("mem", ("low", "normal", "high", "critical"), (50.0, 75.0, 90.0), (45.0, 70.0, 85.0))
DWELL_K = 2


@dataclass(frozen=True)
class DiscState:
    level: int = 0  # current hysteresis level index
    streak: int = 1  # consecutive polls at `level`
    emitted: int = 0  # last emitted level index


def _hysteresis_level(ladder: Ladder, current: int, value: float) -> int:
    s = current
    while s < len(ladder.levels) - 1 and value >= ladder.rising[s]:
        s += 1
    while s > 0 and value < ladder.falling[s - 1]:
        s -= 1
    return s


def step(ladder: Ladder, state: DiscState, value: float, dwell_k: int = DWELL_K) -> tuple[DiscState, str | None]:
    """Advance one poll. Returns (new_state, emitted_level_name_or_None). Pure."""
    level = _hysteresis_level(ladder, state.level, value)
    streak = state.streak + 1 if level == state.level else 1
    emitted = state.emitted
    emit: str | None = None
    if streak >= dwell_k and level != emitted:
        emit = ladder.levels[level]
        emitted = level
    return DiscState(level=level, streak=streak, emitted=emitted), emit
