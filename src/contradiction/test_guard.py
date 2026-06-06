"""M1 guard tests: fake-injected L2 + real ONA. Verifies direct-negation detection with dual
evidence trails, flag-to-human, and no false positives."""
from brain import Brain
from contradiction import ContradictionGuard
from memory import MemoryStore


def test_direct_negation_flags_with_dual_evidence() -> None:
    store = MemoryStore()
    # The system HOLDS a pinned constraint: X is NOT Y.
    store.upsert("<x --> y>", 0.0, 0.9, english="x is definitely not y", now=1.0)
    store.pin("<x --> y>")
    captured: list = []
    with Brain(cycles_per_step=50) as brain:
        guard = ContradictionGuard(brain, store, on_conflict=captured.append)
        # The LLM proposes the opposite: X IS Y.
        conflict = guard.check("<x --> y>. {1.0 0.9}")
    assert conflict is not None, "expected a contradiction"
    assert conflict.term == "<x --> y>"
    # Incoming side (the new claim's evidence):
    assert conflict.incoming.frequency == 1.0
    assert conflict.incoming_statement == "<x --> y>. {1.0 0.9}"
    # Existing side carries ONA's evidence trail (truth + stamp):
    assert conflict.existing.truth is not None and conflict.existing.truth.frequency == 0.0
    assert conflict.existing.stamp, "existing side must carry an evidence stamp"
    # Flagged to the human (no auto-block):
    assert len(captured) == 1 and captured[0] is conflict


def test_no_contradiction_when_consistent() -> None:
    store = MemoryStore()
    store.upsert("<x --> y>", 1.0, 0.9, now=1.0)  # system holds X is Y
    with Brain(cycles_per_step=50) as brain:
        guard = ContradictionGuard(brain, store)
        assert guard.check("<x --> y>. {1.0 0.9}") is None  # LLM agrees -> no conflict


def test_unknown_term_no_false_positive() -> None:
    store = MemoryStore()
    with Brain(cycles_per_step=50) as brain:
        guard = ContradictionGuard(brain, store)
        assert guard.check("<new --> thing>. {1.0 0.9}") is None  # nothing known -> no conflict


if __name__ == "__main__":
    test_direct_negation_flags_with_dual_evidence()
    test_no_contradiction_when_consistent()
    test_unknown_term_no_false_positive()
    print("contradiction/test_guard: OK")
