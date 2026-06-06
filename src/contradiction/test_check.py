"""Unit tests for the pure polarity contradiction logic (no model, no ONA)."""
from brain import Truth
from contradiction.check import is_contradiction


def test_opposite_polarity_flags() -> None:
    assert is_contradiction(Truth(1.0, 0.9), Truth(0.0, 0.9)) is True  # asserts true vs false
    assert is_contradiction(Truth(0.0, 0.9), Truth(1.0, 0.9)) is True  # asserts false vs true


def test_same_polarity_no_flag() -> None:
    assert is_contradiction(Truth(1.0, 0.9), Truth(0.8, 0.9)) is False  # both true
    assert is_contradiction(Truth(0.0, 0.9), Truth(0.1, 0.9)) is False  # both false


def test_low_confidence_existing_ignored() -> None:
    # Opposite polarity, but the existing belief is too weak to count as a contradiction.
    assert is_contradiction(Truth(1.0, 0.9), Truth(0.0, 0.2), min_confidence=0.3) is False


if __name__ == "__main__":
    test_opposite_polarity_flags()
    test_same_polarity_no_flag()
    test_low_confidence_existing_ignored()
    print("contradiction/test_check: OK")
