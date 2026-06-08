"""Unit tests for learned-habit translation (ADR-012). Pure."""
from context.habits import habit_directive, render_habits


def _approved(cat: str) -> str:
    return f"<distracted_hide_{cat} --> [approved]>"


def test_positive_habit() -> None:
    d = habit_directive(_approved("comms"), 1.0, 0.9)        # E = 0.9 >= 0.85
    assert d == ("When you're fragmenting between apps, you've authorized JARVIS to automatically "
                 "hide chat/messaging apps.")


def test_negative_habit_is_a_safety_directive() -> None:
    d = habit_directive(_approved("dev"), 0.0, 0.9)          # E = 0.05 <= 0.15
    assert d == "You've told JARVIS NOT to auto-hide developer apps."


def test_uncertain_habit_is_omitted() -> None:
    assert habit_directive(_approved("comms"), 1.0, 0.5) is None   # E = 0.75, middle -> omit


def test_non_habit_term_is_ignored() -> None:
    assert habit_directive("<steady --> [baseline]>", 1.0, 0.9) is None
    assert habit_directive("<duck --> bird>", 1.0, 0.9) is None


def test_unmapped_category_is_humanized() -> None:
    d = habit_directive(_approved("dev_tools"), 1.0, 0.9)
    assert "dev tools" in d and "_" not in d                 # snake_case sanitized
    d2 = habit_directive(_approved("graphics-design"), 1.0, 0.9)
    assert "graphics design" in d2 and "-" not in d2         # dash captured (non-greedy) + sanitized


def test_pathological_category_fails_safe() -> None:
    assert habit_directive(_approved("!!!"), 1.0, 0.9) is None          # empty after humanize
    assert habit_directive(_approved("x" * 60), 1.0, 0.9) is None       # too long


def test_render_habits_filters_and_headers() -> None:
    beliefs = [
        (_approved("comms"), 1.0, 0.9),     # positive
        (_approved("dev"), 0.0, 0.9),       # negative
        (_approved("media"), 1.0, 0.5),     # uncertain -> omitted
        ("<steady --> [baseline]>", 1.0, 0.9),  # non-habit -> omitted
    ]
    out = render_habits(beliefs)
    assert out.startswith("Learned habits (how the user prefers JARVIS to operate — respect these):")
    assert "hide chat/messaging apps" in out
    assert "NOT to auto-hide developer apps" in out
    assert "media" not in out and "steady" not in out
    assert out.count("- ") == 2                              # only the two confident habits


def test_render_habits_empty() -> None:
    assert render_habits([]) == ""
    assert render_habits([(_approved("comms"), 1.0, 0.5)]) == ""   # all uncertain -> ""


if __name__ == "__main__":
    test_positive_habit()
    test_negative_habit_is_a_safety_directive()
    test_uncertain_habit_is_omitted()
    test_non_habit_term_is_ignored()
    test_unmapped_category_is_humanized()
    test_pathological_category_fails_safe()
    test_render_habits_filters_and_headers()
    test_render_habits_empty()
    print("context/test_habits: OK")
