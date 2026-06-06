"""Proof tests for the Schmitt discretizer: idle silence, exactly-once, flap immunity, spikes."""
from sentinel.schmitt import CPU_LADDER, DiscState, step


def _run(values: list[float], start: DiscState | None = None) -> tuple[DiscState, list[str]]:
    state = start or DiscState()
    emits: list[str] = []
    for value in values:
        state, emit = step(CPU_LADDER, state, value)
        if emit is not None:
            emits.append(emit)
    return state, emits


def test_idle_machine_emits_nothing() -> None:
    assert _run([1, 2, 3, 0, 5, 2])[1] == []


def test_enters_pegged_exactly_once_under_microfluctuation() -> None:
    # Climb to pegged, then jitter 89-91 forever -> 'pegged' emitted exactly once.
    assert _run([90, 90, 89, 91, 90, 89, 91])[1] == ["pegged"]


def test_boundary_flap_does_not_retrigger() -> None:
    # Sit in pegged, then oscillate 84-86 (inside the 80-88 deadband) -> no re-emit.
    _, emits = _run([90, 90, 84, 86, 84, 86, 84])
    assert emits == ["pegged"]


def test_single_poll_spike_rejected_by_dwell() -> None:
    # A lone 1-poll spike to pegged then back to idle -> dwell K=2 not met -> no emit.
    assert _run([2, 90, 2, 2])[1] == []


def test_sustained_high_emits_then_holds() -> None:
    assert _run([60, 60, 60, 60])[1] == ["high"]


if __name__ == "__main__":
    test_idle_machine_emits_nothing()
    test_enters_pegged_exactly_once_under_microfluctuation()
    test_boundary_flap_does_not_retrigger()
    test_single_poll_spike_rejected_by_dwell()
    test_sustained_high_emits_then_holds()
    print("sentinel/test_schmitt: OK")
