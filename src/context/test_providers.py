"""Unit tests for dynamic-context providers (ADR-010). Pure — fixed datetime, no I/O."""
from datetime import datetime

from context.providers import (
    clock_fact,
    foreground_fact,
    render_live_context,
    system_fact,
)

_NOW = datetime(2026, 6, 7, 20, 35)  # Sunday


def test_clock_fact() -> None:
    assert clock_fact(_NOW) == "date/time: Sunday 2026-06-07 20:35 (local)"


def test_system_fact() -> None:
    assert system_fact("cpu=12% mem=63%") == "system: cpu=12% mem=63%"
    assert system_fact("no poll yet") is None
    assert system_fact("") is None
    assert system_fact(None) is None


def test_foreground_fact() -> None:
    assert foreground_fact("dev", "focused") == "foreground=dev attention=focused"
    assert foreground_fact("comms", None) == "foreground=comms"
    assert foreground_fact(None, "focused") is None       # sentinel off / no reading -> omitted


def test_render_clock_only() -> None:
    out = render_live_context(_NOW)
    assert "do NOT memorize" in out
    assert "date/time: Sunday 2026-06-07 20:35 (local)" in out
    assert "system:" not in out and "foreground=" not in out


def test_render_full() -> None:
    out = render_live_context(_NOW, "cpu=5% mem=40%", ("dev", "focused"))
    assert "date/time:" in out and "system: cpu=5% mem=40%" in out
    assert "foreground=dev attention=focused" in out


def test_render_omits_unavailable() -> None:
    out = render_live_context(_NOW, "no poll yet", (None, None))
    assert "system:" not in out and "foreground=" not in out
    assert "date/time:" in out                            # clock always present


if __name__ == "__main__":
    test_clock_fact()
    test_system_fact()
    test_foreground_fact()
    test_render_clock_only()
    test_render_full()
    test_render_omits_unavailable()
    print("context/test_providers: OK")
