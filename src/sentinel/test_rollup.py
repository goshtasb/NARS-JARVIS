"""Proof tests for the watchdog rollup: 4000-event burst -> one 'active'; quiescence -> 'idle'."""
from sentinel.rollup import RollupState, on_event, on_tick


def test_burst_of_4000_collapses_to_single_active() -> None:
    state = RollupState()
    emits: list[str] = []
    state, emit = on_event(state, 0.0)
    if emit:
        emits.append(emit)
    for i in range(1, 4000):  # 3999 more events spread across ~2s
        state, emit = on_event(state, i / 2000.0)
        if emit:
            emits.append(emit)
    assert emits == ["active"], emits
    assert state.count == 4000
    # Writes stop; 1s of quiet -> idle (falling edge).
    state, emit = on_tick(state, state.last_event + 1.0)
    assert emit == "idle" and state.active is False


def test_no_idle_while_still_active() -> None:
    state, _ = on_event(RollupState(), 0.0)
    state, emit = on_tick(state, 0.5)  # only 0.5s quiet < T_QUIET
    assert emit is None and state.active is True


def test_heartbeat_on_long_burst() -> None:
    state, _ = on_event(RollupState(), 0.0)
    state, emit = on_event(state, 31.0, t_max=30.0)  # still active 31s later
    assert emit == "active"


if __name__ == "__main__":
    test_burst_of_4000_collapses_to_single_active()
    test_no_idle_while_still_active()
    test_heartbeat_on_long_burst()
    print("sentinel/test_rollup: OK")
