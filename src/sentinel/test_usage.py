"""ADR-050 slice — passive-usage aggregation (pure, no I/O)."""
from sentinel import summarize_usage
from sentinel.usage import _is_system, app_name


def test_app_name_known_and_fallback() -> None:
    assert app_name("com.todesktop.230313mzl4w4u92") == "Cursor"   # opaque bundle -> known prefix
    assert app_name("com.tinyspeck.slackmacgap") == "Slack"
    assert app_name("com.apple.safari") == "Safari"
    assert app_name("com.acme.fizzbuzz") == "Fizzbuzz"             # unknown -> cleaned component
    assert app_name("") == "an app"


def test_app_name_corrective_iteration_additions() -> None:
    # ADR-050 corrective iteration: the observed-but-unmapped + common power-user apps now resolve
    # (case-insensitive prefix match — note real bundles are mixed-case, e.g. com.apple.Notes).
    assert app_name("com.superhuman.electron") == "Superhuman"
    assert app_name("com.apple.Notes") == "Notes"
    assert app_name("com.apple.iCal") == "Calendar"
    assert app_name("md.obsidian") == "Obsidian"
    assert app_name("com.jetbrains.pycharm") == "PyCharm"


def test_self_observation_is_filtered() -> None:
    # The observer must not observe itself: JARVIS's own UI is excluded from aggregation, retroactively.
    assert _is_system("com.nars.jarvis") is True
    ev = [{"bundle": "com.nars.jarvis", "bucket": "other", "created_at": 1000.0},
          {"bundle": "com.todesktop.x", "bucket": "dev", "created_at": 1100.0}]
    out = summarize_usage(ev, now=1300.0)
    assert "Jarvis" not in out and "Cursor" in out                  # our app dropped, real work kept


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


def test_summarize_filters_system_processes() -> None:
    # ADR-050: SecurityAgent/WindowServer etc. are not "apps you use" — excluded from the mirror.
    ev = [{"bundle": "com.apple.SecurityAgent", "bucket": "other", "created_at": 1000.0},
          {"bundle": "com.todesktop.x", "bucket": "dev", "created_at": 1100.0}]
    out = summarize_usage(ev, now=1300.0)
    assert "Securityagent" not in out and "Cursor" in out
    # a stream of only system processes yields a blank mirror (nothing real observed)
    assert summarize_usage([{"bundle": "com.apple.WindowServer", "bucket": "other", "created_at": 1.0}],
                           now=2.0) == ""
