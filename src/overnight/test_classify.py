"""ADR-031 safety boundary: only read-only catalog actions may run unattended overnight."""
from actions import resolve
from overnight import safe_autonomous


def test_read_only_actions_are_autonomous() -> None:
    assert safe_autonomous(resolve("find_file"))       # kind="query" — Spotlight search, no mutation
    assert safe_autonomous(resolve("report_system"))   # kind="diag" — read-only system report
    assert safe_autonomous(resolve("read_file"))       # kind="work" — read a local document (ADR-032)
    assert safe_autonomous(resolve("summarize_file"))  # kind="work" — read + summarize, scratchpad-only


def test_state_changers_and_gui_and_destructive_are_held() -> None:
    assert not safe_autonomous(resolve("mute"))         # argv — changes system config
    assert not safe_autonomous(resolve("dark_mode"))    # argv — changes system config
    assert not safe_autonomous(resolve("open_app"))     # argv — launches an app
    assert not safe_autonomous(resolve("empty_trash"))  # argv + confirm — destructive
    assert not safe_autonomous(resolve("navigate"))     # agent — GUI navigation
    assert not safe_autonomous(resolve("ax_press"))     # ax — GUI actuation


def test_unknown_action_is_held_by_default() -> None:
    assert not safe_autonomous(resolve("does_not_exist"))  # resolve -> None -> held (default-deny)
    assert not safe_autonomous(None)


if __name__ == "__main__":
    test_read_only_actions_are_autonomous()
    test_state_changers_and_gui_and_destructive_are_held()
    test_unknown_action_is_held_by_default()
    print("overnight/test_classify: OK")
