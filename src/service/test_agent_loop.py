"""Unit tests for the bounded agent-loop routing (ADR-024 Phase 2). Pure: surface resolution
(safe-open + fail-safe) and the next-step decision. The stateful orchestration (hop counter, consent
gate, deadline) is exercised live + via the converse/agent_step seams."""
from service.agent_loop import agent_route, resolve_surface


def test_resolve_surface_known_and_unknown() -> None:
    assert resolve_surface("turn on Do Not Disturb") is not None   # substring match -> Focus pane
    assert resolve_surface("focus") is not None
    assert resolve_surface("open bluetooth please") is not None
    assert resolve_surface("the kitchen fridge") is None           # unknown -> None (fail-safe, refused)
    assert resolve_surface("") is None


def test_agent_route_prefers_actuation_over_navigation() -> None:
    assert agent_route([("navigate", "x"), ("ax_press", "btn_1")]) == ("act", "ax_press", "btn_1")
    assert agent_route([("ax_set_value", "sld_1 45")]) == ("act", "ax_set_value", "sld_1 45")


def test_agent_route_navigates_when_no_actuation() -> None:
    assert agent_route([("navigate", "Focus settings")]) == ("navigate", "Focus settings")


def test_agent_route_gives_up_when_nothing_actionable() -> None:
    assert agent_route([]) == ("giveup",)
    assert agent_route([("set_brightness", "50")]) == ("giveup",)   # a recipe verb isn't an in-loop step


if __name__ == "__main__":
    test_resolve_surface_known_and_unknown()
    test_agent_route_prefers_actuation_over_navigation()
    test_agent_route_navigates_when_no_actuation()
    test_agent_route_gives_up_when_nothing_actionable()
    print("service/test_agent_loop: OK")
