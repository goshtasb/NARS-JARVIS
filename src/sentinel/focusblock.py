"""Focus-block tracking + the intervention-lift KPI ('minutes of focus protected').

A focus block = a contiguous span of STEADY attention. We measure whether ACCEPTED interventions
actually buy focus: median block duration in the window AFTER an accepted nudge vs the window
BEFORE it. Pure — all time is injected (synthetic timestamps, no clock, no sleep).
"""
from __future__ import annotations

from dataclasses import dataclass

WINDOW = 1800.0  # 30-min comparison window on each side of an intervention


@dataclass(frozen=True)
class BlockState:
    in_block: bool = False
    start: float = 0.0


@dataclass(frozen=True)
class Block:
    start: float
    duration: float


def update(state: BlockState, now: float, steady: bool) -> tuple[BlockState, Block | None]:
    """Feed current steadiness; returns new state and a completed Block when a steady span ends."""
    if steady and not state.in_block:
        return BlockState(True, now), None                      # block begins
    if not steady and state.in_block:
        return BlockState(False, 0.0), Block(state.start, now - state.start)  # block ends -> emit
    return state, None                                          # no transition


def close(state: BlockState, now: float) -> Block | None:
    """Flush an open block (e.g. at shutdown) so its time isn't lost."""
    return Block(state.start, now - state.start) if state.in_block else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def lift(blocks: list[Block], interventions: list[tuple[float, bool]],
         window: float = WINDOW) -> dict:
    """Median focus-block duration AFTER vs BEFORE accepted interventions, and the delta
    ('focus protected'). Declined interventions are the implicit control; pre is the within-user
    baseline. Returns seconds; the caller renders minutes."""
    accepted = [ts for ts, ok in interventions if ok]
    pre: list[float] = []
    post: list[float] = []
    for ts in accepted:
        pre += [b.duration for b in blocks if ts - window <= b.start < ts]
        post += [b.duration for b in blocks if ts <= b.start < ts + window]
    pre_med, post_med = _median(pre), _median(post)
    delta = (post_med - pre_med) if (pre_med is not None and post_med is not None) else None
    return {"accepted": len(accepted), "pre_median_s": pre_med,
            "post_median_s": post_med, "delta_s": delta}
