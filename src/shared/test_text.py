"""Unit tests for the shared atom sanitizer."""
from shared.text import atom


def test_atom_sanitization() -> None:
    assert atom("CPU") == "cpu"
    assert atom("Obj Dir!") == "obj_dir"
    assert atom("penicillin safe") == "penicillin_safe"
    assert atom("Tim") == "tim"
    assert atom("@@@") == "_"  # empty after sanitization -> placeholder, never invalid


if __name__ == "__main__":
    test_atom_sanitization()
    print("shared/test_text: OK")
