"""Unit tests for pre-commit hybrid grounding (ADR-013). Pure."""
from context.grounding import _category_of, conflicting_habit, grounding_notice


def _approved(cat: str) -> str:
    return f"<distracted_hide_{cat} --> [approved]>"


def test_category_of_detects_autohide_intent() -> None:
    assert _category_of("the user wants JARVIS to auto-hide their IDE when distracted") == "dev"
    assert _category_of("hide chat apps when I'm fragmenting") == "comms"
    assert _category_of("auto-hide media apps") == "media"


def test_category_of_ignores_non_control_text() -> None:
    assert _category_of("the user hides their feelings") is None     # "hide" but no auto-hide intent
    assert _category_of("the user likes tea") is None
    assert _category_of("the user's name is Ashkan") is None
    assert _category_of("auto-hide my unicorn") is None              # intent but no known category


def test_conflicting_habit_requires_a_confident_governing_habit() -> None:
    fact = "please auto-hide my developer tools when distracted"
    # denied dev habit -> conflict, enabled=False
    assert conflicting_habit(fact, [(_approved("dev"), 0.0, 0.9)]) == ("dev", False)
    # approved dev habit -> still a control-plane conflict, enabled=True
    assert conflicting_habit(fact, [(_approved("dev"), 1.0, 0.9)]) == ("dev", True)
    # uncertain habit -> not confident -> no conflict (save normally)
    assert conflicting_habit(fact, [(_approved("dev"), 1.0, 0.5)]) is None
    # no habit for that category -> no conflict
    assert conflicting_habit(fact, [(_approved("comms"), 0.0, 0.9)]) is None


def test_conflicting_habit_none_for_non_control_fact() -> None:
    assert conflicting_habit("the user likes tea", [(_approved("dev"), 0.0, 0.9)]) is None


def test_grounding_notice_wording() -> None:
    disabled = grounding_notice("dev", False)
    assert "developer apps" in disabled and "disabled" in disabled and "enable it" in disabled
    enabled = grounding_notice("comms", True)
    assert "chat/messaging apps" in enabled and "enabled" in enabled and "disable it" in enabled


if __name__ == "__main__":
    test_category_of_detects_autohide_intent()
    test_category_of_ignores_non_control_text()
    test_conflicting_habit_requires_a_confident_governing_habit()
    test_conflicting_habit_none_for_non_control_fact()
    test_grounding_notice_wording()
    print("context/test_grounding: OK")
