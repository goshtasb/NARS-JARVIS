"""Unit tests for conversational-memory directive extraction (ADR-008). Pure — no model."""
from language.extract import (
    MAX_FACTS,
    memory_acknowledgment,
    split_memory_directives,
)


def test_no_tag_passthrough() -> None:
    clean, facts = split_memory_directives("Just a normal answer, nothing to save.")
    assert clean == "Just a normal answer, nothing to save."
    assert facts == []


def test_single_own_line_tag() -> None:
    clean, facts = split_memory_directives(
        "Nice to meet you, Ashkan!\n[[REMEMBER: the user's name is Ashkan]]")
    assert facts == ["the user's name is Ashkan"]
    assert "REMEMBER" not in clean
    assert clean == "Nice to meet you, Ashkan!"


def test_inline_tag_is_stripped_and_spacing_tidied() -> None:
    clean, facts = split_memory_directives(
        "Got it [[REMEMBER: the user prefers dark mode]] I'll keep that in mind.")
    assert facts == ["the user prefers dark mode"]
    assert "[[" not in clean and "  " not in clean       # gap collapsed
    assert clean == "Got it I'll keep that in mind."


def test_multiple_tags_and_case_insensitive_spacing() -> None:
    clean, facts = split_memory_directives(
        "Sure.\n[[ remember : the user is a pilot ]]\n[[REMEMBER: the user lives in Berlin]]")
    assert facts == ["the user is a pilot", "the user lives in Berlin"]
    assert clean == "Sure."


def test_mixed_turn_answer_plus_save() -> None:
    # "my name is Ashkan, what's the weather?" -> the model answers AND records the name.
    clean, facts = split_memory_directives(
        "It's sunny and 22°C today. [[REMEMBER: the user's name is Ashkan]]")
    assert facts == ["the user's name is Ashkan"]
    assert clean == "It's sunny and 22°C today."


def test_empty_and_overlong_directives_ignored() -> None:
    clean, facts = split_memory_directives(
        "ok [[REMEMBER:   ]] and [[REMEMBER: " + "x" * 500 + "]]")
    assert facts == []
    assert "REMEMBER" not in clean


def test_caps_at_max_facts_and_dedups() -> None:
    body = "".join(f"[[REMEMBER: fact {i}]]" for i in range(10))
    _, facts = split_memory_directives(body)
    assert len(facts) == MAX_FACTS
    dup = "[[REMEMBER: same]][[REMEMBER: same]]"
    _, facts2 = split_memory_directives(dup)
    assert facts2 == ["same"]


def test_acknowledgment() -> None:
    assert memory_acknowledgment([]) == ""
    assert memory_acknowledgment(["a", "b"]) == "(Saved: a; b)"


if __name__ == "__main__":
    test_no_tag_passthrough()
    test_single_own_line_tag()
    test_inline_tag_is_stripped_and_spacing_tidied()
    test_multiple_tags_and_case_insensitive_spacing()
    test_mixed_turn_answer_plus_save()
    test_empty_and_overlong_directives_ignored()
    test_caps_at_max_facts_and_dedups()
    test_acknowledgment()
    print("language/test_extract: OK")
