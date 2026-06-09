"""Unit tests for HabitStore introspection/pruning surface (ADR-027): list_all + delete."""
from habits import HabitStore


def test_list_all_and_delete() -> None:
    s = HabitStore(":memory:")
    s.record("h09_mute", "h09", "mute", "", 1.0, 0.9)
    s.record("h14_dark_mode", "h14", "dark_mode", "", 1.0, 0.5)
    rows = s.list_all()
    assert {r["key"] for r in rows} == {"h09_mute", "h14_dark_mode"}
    assert all({"key", "bucket", "action", "arg", "frequency", "confidence"} <= set(r) for r in rows)
    s.delete("h09_mute")
    assert {r["key"] for r in s.list_all()} == {"h14_dark_mode"}
    s.close()


if __name__ == "__main__":
    test_list_all_and_delete()
    print("habits/test_store: OK")
