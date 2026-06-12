"""ADR-050 slice — passive-usage aggregation (pure, no I/O)."""
from sentinel import summarize_usage
from sentinel.usage import app_name


def test_app_name_known_and_fallback() -> None:
    assert app_name("com.todesktop.230313mzl4w4u92") == "Cursor"   # opaque bundle -> known prefix
    assert app_name("com.tinyspeck.slackmacgap") == "Slack"
    assert app_name("com.apple.safari") == "Safari"
    assert app_name("com.acme.fizzbuzz") == "Fizzbuzz"             # unknown -> cleaned component
    assert app_name("") == "an app"


def test_summarize_empty_is_blank() -> None:
    assert summarize_usage([], now=1000.0) == ""


def test_summarize_aggregates_time_and_apps() -> None:
    # Cursor 9–9:50 (3000s), Slack 9:50–10 (600s) -> Cursor leads; one row has no successor (capped by now).
    ev = [{"bundle": "com.todesktop.x", "bucket": "dev", "created_at": 1000.0},
          {"bundle": "com.tinyspeck.slackmacgap", "bucket": "comms", "created_at": 4000.0},
          {"bundle": "com.todesktop.x", "bucket": "dev", "created_at": 4600.0}]
    out = summarize_usage(ev, now=4800.0)
    assert "What I've noticed about your computer use" in out
    assert "3 app switches" in out
    assert "Cursor" in out and "Slack" in out                  # friendly names surfaced
    assert "dev" in out                                        # category breakdown
    assert "never your screen contents" in out                 # privacy line present
    # Cursor (3000+200s) should rank before Slack (600s) in "Most of your time"
    assert out.index("Cursor") < out.index("Slack")
