"""NARS-gated autonomy: the two-condition gate (pure) + the full earn-then-lose loop on real ONA."""
from brain import Brain
from service.autonomy import approved_term, evidence_belief, expectation, gate_passes


def test_gate_is_two_conditions() -> None:
    assert gate_passes(1.0, 0.90)                 # enough evidence AND favorable -> autonomous
    assert not gate_passes(1.0, 0.50)             # favorable but too little evidence
    # THE lock: six rejections also reach confidence ~0.857, but polarity is negative -> NEVER fire.
    assert not gate_passes(0.0, 0.90)
    assert abs(expectation(0.0, 0.90) - 0.05) < 1e-9
    assert abs(expectation(1.0, 0.90) - 0.95) < 1e-9


def test_evidence_belief_asymmetric_weights() -> None:
    assert evidence_belief("comms", True) == "<distracted_hide_comms --> [approved]>. {1.0 0.5}"
    assert evidence_belief("comms", False) == "<distracted_hide_comms --> [approved]>. {0.0 0.9}"


def test_consent_earns_then_loses_autonomy() -> None:
    # The learning loop end-to-end on real ONA: ~6 approvals earn autonomy; a couple of heavy
    # declines revoke it (the asymmetric safety ratchet).
    with Brain(cycles_per_step=20) as b:
        q = approved_term("comms") + "?"
        assert b.ask(q) is None                   # Day 1: no belief -> gate can't pass

        for _ in range(6):                        # six explicit approvals
            b.add_belief(evidence_belief("comms", True))
        ans = b.ask(q)
        assert ans is not None and ans.truth is not None
        assert gate_passes(ans.truth.frequency, ans.truth.confidence), ans.truth  # autonomy earned

        for _ in range(2):                        # a couple of heavy declines
            b.add_belief(evidence_belief("comms", False))
        ans2 = b.ask(q)
        assert ans2 is not None and ans2.truth is not None
        assert not gate_passes(ans2.truth.frequency, ans2.truth.confidence), ans2.truth  # revoked


if __name__ == "__main__":
    test_gate_is_two_conditions()
    test_evidence_belief_asymmetric_weights()
    test_consent_earns_then_loses_autonomy()
    print("service/test_autonomy: OK")
