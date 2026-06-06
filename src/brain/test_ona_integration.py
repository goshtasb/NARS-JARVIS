"""Integration test: drive the real ONA reasoner through the Brain wrapper.

Imperative Shell test — requires the built NAR binary (OpenNARS-for-Applications/NAR).
"""
from brain import Brain


def test_deduction_end_to_end() -> None:
    with Brain(cycles_per_step=100) as brain:
        brain.add_belief("<a --> b>.")
        brain.add_belief("<b --> c>.")
        answer = brain.ask("<a --> c>?")
    assert answer is not None, "expected a derived answer"
    assert answer.term == "<a --> c>"
    assert answer.truth is not None
    # Truth_Deduction: confidence = c1 * c2 * f = 0.9 * 0.9 * 1.0 = 0.81
    assert abs(answer.truth.confidence - 0.81) < 1e-6, answer
    # Evidence trail: derived from input #1 and input #2.
    assert set(answer.stamp) == {1, 2}, answer


if __name__ == "__main__":
    test_deduction_end_to_end()
    print("brain/test_ona_integration: OK")
