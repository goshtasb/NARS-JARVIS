"""Unit tests for habit quantization (ADR-026). Pure — bucketing, term-safe keys, eligibility."""
from datetime import datetime

from habits import (
    bucket_label,
    describe_habit,
    eligible,
    evidence_count,
    habit_evidence,
    habit_key,
    habit_term,
    time_bucket,
)


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


def test_bucket_label_is_12_hour() -> None:
    assert bucket_label("h09") == "9:00 AM"
    assert bucket_label("h14") == "2:00 PM"
    assert bucket_label("h00") == "12:00 AM"
    assert bucket_label("h12") == "12:00 PM"


def test_evidence_count_from_confidence() -> None:
    # ONA c = w/(w+1) -> w ≈ c/(1-c); honest count, never a probability.
    assert evidence_count(0.5) == 1
    assert evidence_count(0.833) == 5
    assert evidence_count(0.857) == 6


def test_describe_habit_is_human_readable() -> None:
    assert describe_habit("set_brightness", "100", "h09") == "set brightness 100 around 9:00 AM"
    assert describe_habit("mute", "", "h14") == "mute around 2:00 PM"


if __name__ == "__main__":
    test_time_bucket_is_hour_of_day()
    test_bucket_label_is_12_hour()
    test_evidence_count_from_confidence()
    test_describe_habit_is_human_readable()
    test_habit_key_is_canonical_and_term_safe()
    test_habit_evidence_uses_asymmetric_weights()
    test_eligible_only_safe_repeatable_state_changers()
    print("habits/test_quantize: OK")
