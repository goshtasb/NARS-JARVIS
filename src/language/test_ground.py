"""Unit tests for the pure grounding/dedup core (synthetic vectors; no model needed)."""
from language.ground import cosine_similarity, resolve_atom


def test_cosine() -> None:
    assert abs(cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0]) - 0.0) < 1e-9
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_resolve_reuses_similar() -> None:
    existing = {"car": [1.0, 0.0, 0.0]}
    atom, created = resolve_atom("automobile", [0.99, 0.01, 0.0], existing, threshold=0.95)
    assert atom == "car" and created is False


def test_resolve_creates_distinct() -> None:
    existing = {"car": [1.0, 0.0, 0.0]}
    atom, created = resolve_atom("banana", [0.0, 1.0, 0.0], existing, threshold=0.95)
    assert atom == "banana" and created is True


if __name__ == "__main__":
    test_cosine()
    test_resolve_reuses_similar()
    test_resolve_creates_distinct()
    print("language/test_ground: OK")
