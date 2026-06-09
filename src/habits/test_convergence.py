"""The heart of ADR-026: NARS habit convergence against a REAL ONA brain. Proves the ramp arms, a
single denial collapses it, and dense (singleton) context never converges — the whole thesis."""
from brain import Brain
from habits import context_key, habit_evidence, habit_term
from service.autonomy import gate_passes


def _truth(brain, key):
    ans = brain.ask(habit_term(key) + "?")
    return (ans.truth.frequency, ans.truth.confidence) if ans and ans.truth else (None, None)


def test_repeated_confirmations_arm_the_gate() -> None:
    with Brain(cycles_per_step=50) as b:
        key = "h09_dark_mode"
        for _ in range(8):                       # the same recurring term -> w accumulates past 0.85
            b.add_belief(habit_evidence(key, True))
        f, c = _truth(b, key)
        assert f is not None and gate_passes(f, c), (f, c)


def test_one_confirmation_does_not_arm() -> None:
    with Brain(cycles_per_step=50) as b:
        b.add_belief(habit_evidence("h09_dark_mode", True))   # one YES -> conf 0.5, E 0.75
        assert not gate_passes(*_truth(b, "h09_dark_mode"))


def test_one_denial_collapses_an_armed_habit() -> None:
    with Brain(cycles_per_step=50) as b:
        key = "h09_dark_mode"
        for _ in range(8):
            b.add_belief(habit_evidence(key, True))
        assert gate_passes(*_truth(b, key))                   # armed
        b.add_belief(habit_evidence(key, False))              # one heavy NO {0.0 0.9}
        assert not gate_passes(*_truth(b, key))               # collapsed (safety ratchet)


def test_dense_context_never_converges() -> None:
    # The thesis: a unique context per event keeps w=1 forever -> conf 0.5, E 0.75, never armed.
    with Brain(cycles_per_step=50) as b:
        for i in range(5):
            b.add_belief(habit_evidence(f"h09_open_app_app{i}", True))
        for i in range(5):
            assert not gate_passes(*_truth(b, f"h09_open_app_app{i}"))


def test_context_term_converges_like_a_coarse_term() -> None:
    # ADR-028: the full-context term is still COARSE (binary day + app enum), so it recurs and arms —
    # unlike the dense singletons above. This is what makes "mute in Zoom on weekdays" fireable.
    with Brain(cycles_per_step=50) as b:
        key = context_key("h16", "mute", "", "weekday", "app_zoom")   # "h16_mute_weekday_app_zoom"
        for _ in range(8):                       # same context recurs -> w accumulates past 0.85
            b.add_belief(habit_evidence(key, True))
        f, c = _truth(b, key)
        assert f is not None and gate_passes(f, c), (f, c)


if __name__ == "__main__":
    test_repeated_confirmations_arm_the_gate()
    test_one_confirmation_does_not_arm()
    test_one_denial_collapses_an_armed_habit()
    test_dense_context_never_converges()
    test_context_term_converges_like_a_coarse_term()
    print("habits/test_convergence: OK")
