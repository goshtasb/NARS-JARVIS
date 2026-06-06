"""The Autonomy-Eligibility Predicate (M3 Phase A). Overrides ONA's permissive defaults.

ONA ships tuned for game agents (DECISION_THRESHOLD 0.501, MOTOR_BABBLING_CHANCE 0.2). On a live
host that is catastrophic, so this policy layer governs C4: babbling OFF, plus strict floors an
ONA decision must clear to act autonomously. Below ANY floor => Suggestion-Only Mode.
"""
from __future__ import annotations

from dataclasses import dataclass

# Overrides ONA's default 0.2. Applied to ONA via `*motorbabbling=0.0` when execution is wired.
MOTOR_BABBLING_CHANCE = 0.0


@dataclass(frozen=True)
class AutonomyPolicy:
    min_confidence: float = 0.80
    min_frequency: float = 0.90
    min_observations: int = 10
    min_confirmations: int = 5


@dataclass(frozen=True)
class DecisionStats:
    confidence: float
    frequency: float
    observations: int
    confirmations: int


def is_autonomous(policy: AutonomyPolicy, stats: DecisionStats) -> bool:
    """Strict AND of all safety floors. Pure. False => the proposal stays Suggestion-Only."""
    return (
        stats.confidence >= policy.min_confidence
        and stats.frequency >= policy.min_frequency
        and stats.observations >= policy.min_observations
        and stats.confirmations >= policy.min_confirmations
    )
