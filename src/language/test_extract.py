"""Unit tests for conversational-memory directive extraction (ADR-008). Pure — no model."""
from language.extract import (
    MAX_FACTS,
    filter_known,
    filter_semantic,
    memory_acknowledgment,
    split_forget_directives,
    split_memory_directives,
    strip_acknowledgment,
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


# ── filter_known: the deterministic context-echo guard (ADR-008 follow-up) ──
def test_filter_known_drops_verbatim_echo() -> None:
    known = ["the user's name is Ashkan", "ducks are birds"]
    facts = ["the user's name is Ashkan", "ducks are birds"]
    assert filter_known(facts, known) == []          # pure-echo turn -> nothing survives


def test_filter_known_normalizes_near_matches() -> None:
    known = ["the user's name is Ashkan"]
    # case, leading article, and trailing punctuation variations all normalize to the same key
    assert filter_known(["User's name is Ashkan."], known) == []
    assert filter_known(["THE USER'S NAME IS ASHKAN"], known) == []


def test_filter_known_keeps_genuinely_new() -> None:
    known = ["the user's name is Ashkan"]
    facts = ["the user's name is Ashkan", "the user lives in Berlin"]
    assert filter_known(facts, known) == ["the user lives in Berlin"]


def test_filter_known_empty_known_is_passthrough() -> None:
    facts = ["the user prefers tabs"]
    assert filter_known(facts, []) == facts


# ── filter_semantic: the embedding guard for PARAPHRASE echoes normalization can't catch ──
def _fake_embed(text: str) -> list[float]:
    """Concept-keyed unit vectors so paraphrases of the same fact collide, distinct facts don't."""
    t = text.lower()
    if "ashkan" in t:
        return [1.0, 0.0, 0.0]   # any phrasing of the name fact -> same vector
    if "berlin" in t:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


def test_filter_semantic_drops_paraphrase_echo() -> None:
    # the exact live failure: injected "my name is Ashkan" re-tagged as "the user's name is Ashkan"
    kept = filter_semantic(["the user's name is Ashkan"], ["my name is Ashkan"], _fake_embed)
    assert kept == []


def test_filter_semantic_keeps_distinct_fact() -> None:
    kept = filter_semantic(["the user lives in Berlin"], ["my name is Ashkan"], _fake_embed)
    assert kept == ["the user lives in Berlin"]


def test_filter_semantic_mixed() -> None:
    kept = filter_semantic(["the user's name is Ashkan", "the user lives in Berlin"],
                           ["my name is Ashkan"], _fake_embed)
    assert kept == ["the user lives in Berlin"]


def test_filter_semantic_empty_known_passthrough() -> None:
    assert filter_semantic(["x"], [], _fake_embed) == ["x"]


# ── strip_acknowledgment: the TTS payload must not voice "(Saved: …)" ──
def test_strip_acknowledgment_removes_trailing_suffix() -> None:
    assert strip_acknowledgment("Nice to meet you, Ashkan!\n(Saved: the user's name is Ashkan)") \
        == "Nice to meet you, Ashkan!"


def test_strip_acknowledgment_noop_without_suffix() -> None:
    assert strip_acknowledgment("Paris.") == "Paris."


def test_strip_acknowledgment_inverse_of_acknowledgment() -> None:
    reply, facts = "Good to know.", ["the user prefers tabs over spaces"]
    spoken = f"{reply}\n{memory_acknowledgment(facts)}"
    assert strip_acknowledgment(spoken) == reply


# ── [[FORGET]] directive (ADR-009), mirrors [[REMEMBER]] ──
def test_split_forget_directives() -> None:
    clean, forgets = split_forget_directives("Okay, done.\n[[FORGET: the user likes tea]]")
    assert forgets == ["the user likes tea"]
    assert clean == "Okay, done." and "FORGET" not in clean


def test_remember_and_forget_coexist() -> None:
    reply = "Updated.\n[[FORGET: the user likes tea]]\n[[REMEMBER: the user likes coffee]]"
    clean1, remembers = split_memory_directives(reply)
    clean2, forgets = split_forget_directives(clean1)
    assert remembers == ["the user likes coffee"]
    assert forgets == ["the user likes tea"]
    assert clean2 == "Updated." and "[[" not in clean2


if __name__ == "__main__":
    test_no_tag_passthrough()
    test_single_own_line_tag()
    test_inline_tag_is_stripped_and_spacing_tidied()
    test_multiple_tags_and_case_insensitive_spacing()
    test_mixed_turn_answer_plus_save()
    test_empty_and_overlong_directives_ignored()
    test_caps_at_max_facts_and_dedups()
    test_acknowledgment()
    test_filter_known_drops_verbatim_echo()
    test_filter_known_normalizes_near_matches()
    test_filter_known_keeps_genuinely_new()
    test_filter_known_empty_known_is_passthrough()
    test_filter_semantic_drops_paraphrase_echo()
    test_filter_semantic_keeps_distinct_fact()
    test_filter_semantic_mixed()
    test_filter_semantic_empty_known_passthrough()
    test_strip_acknowledgment_removes_trailing_suffix()
    test_strip_acknowledgment_noop_without_suffix()
    test_strip_acknowledgment_inverse_of_acknowledgment()
    test_split_forget_directives()
    test_remember_and_forget_coexist()
    print("language/test_extract: OK")
