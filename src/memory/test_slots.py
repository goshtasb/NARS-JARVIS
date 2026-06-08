"""Unit tests for the single-valued-slot registry (ADR-009). Pure — no DB, no model."""
from memory.slots import same_single_valued_slot, slot_of


def test_slot_of_name() -> None:
    assert slot_of("the user's name is Ashkan") == ("name", "ashkan")
    assert slot_of("the user goes by Sam") == ("name", "sam")


def test_slot_of_indentation() -> None:
    assert slot_of("the user prefers tabs over spaces") == ("indentation_pref", "tabs")
    assert slot_of("the user prefers spaces over tabs") == ("indentation_pref", "spaces")


def test_slot_of_unknown_predicate_is_none() -> None:
    # "likes" is multi-valued -> deliberately NOT a slot -> keep both
    assert slot_of("the user likes tea") is None
    assert slot_of("the user likes coffee") is None


def test_name_change_is_a_conflict() -> None:
    assert same_single_valued_slot("my name is Ashkan", "my name is Sam") is True


def test_indentation_flip_is_a_conflict() -> None:
    assert same_single_valued_slot("the user prefers tabs over spaces",
                                   "the user prefers spaces over tabs") is True


def test_tea_vs_coffee_is_not_a_conflict() -> None:
    # the load-bearing case: high topical cosine, but NO single-valued slot -> keep both
    assert same_single_valued_slot("the user likes tea", "the user likes coffee") is False


def test_same_slot_same_value_is_not_a_conflict() -> None:
    # a restatement, not a contradiction
    assert same_single_valued_slot("my name is Ashkan", "the user's name is Ashkan") is False


def test_different_slots_not_a_conflict() -> None:
    assert same_single_valued_slot("my name is Ashkan", "the user lives in Berlin") is False


def test_lives_in_and_editor_and_age() -> None:
    assert same_single_valued_slot("the user lives in Berlin", "the user lives in Munich") is True
    assert same_single_valued_slot("the user uses vim", "the user uses emacs") is True
    assert same_single_valued_slot("the user is 30 years old", "the user is 31 years old") is True


if __name__ == "__main__":
    test_slot_of_name()
    test_slot_of_indentation()
    test_slot_of_unknown_predicate_is_none()
    test_name_change_is_a_conflict()
    test_indentation_flip_is_a_conflict()
    test_tea_vs_coffee_is_not_a_conflict()
    test_same_slot_same_value_is_not_a_conflict()
    test_different_slots_not_a_conflict()
    test_lives_in_and_editor_and_age()
    print("memory/test_slots: OK")
