"""Autonomy-predicate tests: babbling off, coincidence trapped, each floor gates independently."""
from execution.autonomy import MOTOR_BABBLING_CHANCE, AutonomyPolicy, DecisionStats, is_autonomous


def test_motor_babbling_disabled() -> None:
    assert MOTOR_BABBLING_CHANCE == 0.0


def test_low_confidence_coincidence_not_autonomous() -> None:
    # 2 observations, low confidence, no confirmations -> Suggestion-Only.
    stats = DecisionStats(confidence=0.45, frequency=1.0, observations=2, confirmations=0)
    assert is_autonomous(AutonomyPolicy(), stats) is False


def test_strong_proven_habit_is_autonomous() -> None:
    stats = DecisionStats(confidence=0.90, frequency=0.95, observations=20, confirmations=8)
    assert is_autonomous(AutonomyPolicy(), stats) is True


def test_each_floor_gates_independently() -> None:
    policy = AutonomyPolicy()
    assert is_autonomous(policy, DecisionStats(0.90, 0.95, 20, 8)) is True
    assert is_autonomous(policy, DecisionStats(0.79, 0.95, 20, 8)) is False  # confidence floor
    assert is_autonomous(policy, DecisionStats(0.90, 0.89, 20, 8)) is False  # frequency floor
    assert is_autonomous(policy, DecisionStats(0.90, 0.95, 9, 8)) is False  # observation floor
    assert is_autonomous(policy, DecisionStats(0.90, 0.95, 20, 4)) is False  # confirmation floor


if __name__ == "__main__":
    test_motor_babbling_disabled()
    test_low_confidence_coincidence_not_autonomous()
    test_strong_proven_habit_is_autonomous()
    test_each_floor_gates_independently()
    print("execution/test_autonomy: OK")
