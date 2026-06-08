"""Unit tests for the volatile-fact guard (ADR-010). Pure."""
from context.volatile import is_volatile


def test_volatile_time_and_date() -> None:
    assert is_volatile("the current time is 8 pm")
    assert is_volatile("it's 8pm")
    assert is_volatile("it is 8:30 pm")
    assert is_volatile("the time is 8 pm")
    assert is_volatile("today is Monday")
    assert is_volatile("the date is June 7")
    assert is_volatile("the user is in a meeting right now")


def test_volatile_system_load() -> None:
    assert is_volatile("cpu usage is 80%")
    assert is_volatile("memory is at 60%")


def test_not_volatile_durable_facts() -> None:
    assert not is_volatile("the user's name is Ashkan")
    assert not is_volatile("the user likes tea")
    assert not is_volatile("the user prefers tabs over spaces")
    assert not is_volatile("the user is a pilot")
    assert not is_volatile("the user lives in Berlin")


if __name__ == "__main__":
    test_volatile_time_and_date()
    test_volatile_system_load()
    test_not_volatile_durable_facts()
    print("context/test_volatile: OK")
