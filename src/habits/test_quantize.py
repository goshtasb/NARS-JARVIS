"""Unit tests for habit quantization (ADR-026). Pure — bucketing, term-safe keys, eligibility."""
from datetime import datetime

from habits import eligible, habit_evidence, habit_key, habit_term, time_bucket


def test_time_bucket_is_hour_of_day() -> None:
    assert time_bucket(datetime(2026, 6, 9, 9, 3, 17)) == "h09"
    assert time_bucket(datetime(2026, 6, 9, 14, 59)) == "h14"


def test_habit_key_is_canonical_and_term_safe() -> None:
    assert habit_key("h09", "set_brightness", "100") == "h09_set_brightness_100"
    assert habit_key("h09", "open_app", "Google Chrome") == "h09_open_app_google_chrome"  # slugged
    assert habit_key("h09", "dark_mode") == "h09_dark_mode"                                # no arg
    # the resulting Narsese term is valid (only [a-z0-9_] inside the atom)
    assert habit_term(habit_key("h09", "open_app", "Google Chrome")).startswith("<habit_h09_open_app_google_chrome")


def test_habit_evidence_uses_asymmetric_weights() -> None:
    assert habit_evidence("h09_dark_mode", True).endswith("{1.0 0.5}")    # slow-climb YES
    assert habit_evidence("h09_dark_mode", False).endswith("{0.0 0.9}")   # fast-collapse NO


def test_eligible_only_safe_repeatable_state_changers() -> None:
    assert eligible("dark_mode") and eligible("open_app") and eligible("set_brightness")
    assert not eligible("find_file")       # read-only query
    assert not eligible("report_system")   # read-only diag
    assert not eligible("empty_trash")     # destructive (confirm)
    assert not eligible("nonexistent")


if __name__ == "__main__":
    test_time_bucket_is_hour_of_day()
    test_habit_key_is_canonical_and_term_safe()
    test_habit_evidence_uses_asymmetric_weights()
    test_eligible_only_safe_repeatable_state_changers()
    print("habits/test_quantize: OK")
