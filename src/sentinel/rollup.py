"""Watchdog activity rollup — pure Functional Core (S-02). Trailing-edge debounce / coalescing.

A directory's filesystem activity is a 2-state signal {idle, active}. A burst of raw events
collapses to ONE 'active' emit (rising edge); 'idle' is emitted after a quiet period (falling
edge); a long-running burst re-emits a heartbeat. Pure: explicit timestamps, no real clock.
"""
from __future__ import annotations

from dataclasses import dataclass

T_QUIET = 1.0  # seconds of silence before active -> idle
T_MAX = 30.0  # max active burst before a heartbeat re-emit


@dataclass(frozen=True)
class RollupState:
    active: bool = False
    count: int = 0
    last_event: float = 0.0
    active_since: float = 0.0


def on_event(state: RollupState, now: float, t_max: float = T_MAX) -> tuple[RollupState, str | None]:
    """A raw filesystem event at time `now`. Returns (state, 'active' | None)."""
    if not state.active:
        return RollupState(True, 1, now, now), "active"  # rising edge -> emit once
    if now - state.active_since >= t_max:
        return RollupState(True, state.count + 1, now, now), "active"  # long-burst heartbeat
    return RollupState(True, state.count + 1, now, state.active_since), None  # suppressed


def on_tick(state: RollupState, now: float, t_quiet: float = T_QUIET) -> tuple[RollupState, str | None]:
    """A timer tick. Emits 'idle' once the directory has been quiet for `t_quiet`."""
    if state.active and now - state.last_event >= t_quiet:
        return RollupState(False, 0, state.last_event, 0.0), "idle"  # falling edge
    return state, None
