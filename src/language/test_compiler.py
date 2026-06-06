"""Unit tests for the pure claim->Narsese compiler (no model needed)."""
from language.compiler import claims_to_narsese, to_narsese
from language.schema import PropertyClaim, RelationClaim, parse_claims


def test_relation_isa() -> None:
    assert to_narsese(RelationClaim("Tim", "IsA", "duck")) == "<tim --> duck>."


def test_relation_verb() -> None:
    assert to_narsese(RelationClaim("tim", "likes", "water")) == "<(tim * water) --> likes>."


def test_property() -> None:
    assert to_narsese(PropertyClaim("tim", "happy")) == "<tim --> [happy]>."


def test_negated() -> None:
    assert to_narsese(PropertyClaim("tim", "happy", negated=True)) == "<tim --> [happy]>. {0.0 0.9}"


def test_parse_then_compile() -> None:
    text = (
        '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"},'
        '{"type":"NegatedPropertyClaim","subject":"Tim","value":"hungry"}]'
    )
    assert claims_to_narsese(parse_claims(text)) == [
        "<tim --> duck>.",
        "<tim --> [hungry]>. {0.0 0.9}",
    ]


if __name__ == "__main__":
    test_relation_isa()
    test_relation_verb()
    test_property()
    test_negated()
    test_parse_then_compile()
    print("language/test_compiler: OK")
