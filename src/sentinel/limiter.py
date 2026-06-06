"""Token-bucket rate limiter — pure Functional Core (S-02). The hard backstop.

Caps Narsese emission into ONA so the 40-slot attention buffer can never flood, even under a
misconfigured discretizer. Pure: explicit timestamps. Overflow is coalesced + logged by the
shell (never silently dropped).
"""
from __future__ import annotations

from dataclasses import dataclass

RATE = 5.0  # tokens (events) admitted per second into ONA
CAPACITY = 10.0  # burst capacity


@dataclass(frozen=True)
class BucketState:
    tokens: float = CAPACITY
    last_refill: float = 0.0


def try_consume(
    state: BucketState, now: float, rate: float = RATE, capacity: float = CAPACITY
) -> tuple[BucketState, bool]:
    """Try to admit one event at `now`. Returns (new_state, allowed). Pure."""
    tokens = min(capacity, state.tokens + max(0.0, now - state.last_refill) * rate)
    if tokens >= 1.0:
        return BucketState(tokens - 1.0, now), True
    return BucketState(tokens, now), False
