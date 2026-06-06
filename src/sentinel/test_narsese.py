"""Unit tests for the pure sentinel Narsese builders."""
from sentinel.narsese import activity_event, signal_event


def test_signal_event() -> None:
    assert signal_event("cpu", "pegged") == "<cpu --> [pegged]>. :|:"


def test_activity_event_sanitizes() -> None:
    assert activity_event("Obj Dir!", "active") == "<obj_dir --> [active]>. :|:"


if __name__ == "__main__":
    test_signal_event()
    test_activity_event_sanitizes()
    print("sentinel/test_narsese: OK")
