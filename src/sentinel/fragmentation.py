"""Dual-plane fragmentation funnel — Functional Core (S-02). Pure; time is a parameter, never a clock.

Measurement plane: a debounce-FREE ring of switch timestamps captures EVERY micro-switch and yields
the instantaneous switch-rate over a rolling window (this is what makes real fragmentation visible
and the focus-block KPI honest). Ingestion plane: a Schmitt trigger (reused from schmitt.py) maps
that rate to a bounded level and emits ONLY on a hysteresis crossing — so the isolated Sentinel ONA
sees a trickle of state transitions, never the firehose. Both take an explicit `now`/value, so unit
tests feed synthetic timestamps with zero sleep().
"""
from __future__ import annotations

from dataclasses import dataclass

from .schmitt import Ladder

WINDOW = 120.0  # rolling window (seconds) over which switch-rate is measured

# Rate (switches per WINDOW) -> attention level. Hysteresis deadband prevents flapping. Tunable dials.
FRAGMENTATION_LADDER = Ladder(
    "attention",
    ("focused", "light", "fragmented", "thrashing"),
    rising=(3.0, 9.0, 18.0),
    falling=(2.0, 7.0, 15.0),
)


@dataclass(frozen=True)
class RingState:
    """The measurement plane: timestamps of recent context switches (full fidelity)."""
    times: tuple[float, ...] = ()


def record(state: RingState, now: float, window: float = WINDOW) -> RingState:
    """Append a switch at `now`, evicting any older than the window. Boundary is exclusive
    (an event exactly at now-window is dropped). Pure."""
    kept = tuple(t for t in state.times if t > now - window) + (now,)
    return RingState(kept)


def rate(state: RingState, now: float, window: float = WINDOW) -> int:
    """Switches within the rolling window ending at `now`. Pure, no clock."""
    return sum(1 for t in state.times if t > now - window)
