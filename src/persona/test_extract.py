"""ADR-036 extractor: the 7B emits JSON, code validates against the closed vocabulary and renders the
Narsese. Malformed JSON / out-of-vocab / bad numbers must never reach the NAR. Fake `generate`, no model."""
from persona import extract
from persona.extract import parse_items


def test_parse_validates_against_closed_vocab() -> None:
    raw = ('[{"predicate":"format_directive","value":"omit_greeting_prose","freq":1.0,"conf":0.9},'
           ' {"predicate":"format_directive","value":"DROP TABLE","freq":1,"conf":1},'   # not in vocab
           ' {"predicate":"hacker","value":"x","freq":1,"conf":1}]')                     # not in vocab
    out = parse_items(raw)
    assert out == [("<format_directive --> omit_greeting_prose>", 1.0, 0.9)]             # only the valid one


def test_parse_clamps_and_handles_garbage() -> None:
    assert parse_items("not json at all") == []
    assert parse_items("") == []
    out = parse_items('[{"predicate":"current_focus","value":"local_development","freq":5,"conf":-2}]')
    assert out == [("<current_focus --> local_development>", 1.0, 0.0)]                  # clamped to [0,1]


def test_extract_uses_injected_generate() -> None:
    calls = {"n": 0}
    def fake(system, user, max_tokens):
        calls["n"] += 1
        assert "closed vocabulary" in system and "Events:" in user
        return '[{"predicate":"current_focus","value":"local_development","freq":1.0,"conf":0.8}]'
    out = extract(["worked in the local repo all afternoon"], fake)
    assert calls["n"] == 1 and out == [("<current_focus --> local_development>", 1.0, 0.8)]
    assert extract([], fake) == []                          # no events -> no model call
    assert calls["n"] == 1


if __name__ == "__main__":
    test_parse_validates_against_closed_vocab()
    test_parse_clamps_and_handles_garbage()
    test_extract_uses_injected_generate()
    print("persona/test_extract: OK")
