"""Surprise-detection tests (deterministic; real ONA with an injected strong prior)."""
from brain import Brain, Truth
from sentinel.surprise import SurpriseDetector, expectation


def test_expectation_formula() -> None:
    assert abs(expectation(Truth(1.0, 0.9)) - 0.95) < 1e-9
    assert abs(expectation(Truth(0.0, 0.9)) - 0.05) < 1e-9


def test_strong_prior_then_anomaly_trips() -> None:
    surprises: list = []
    with Brain(cycles_per_step=50) as brain:
        brain.add_belief("<cpu --> [pegged]>. {0.0 0.9}")  # prior: usually NOT pegged
        detector = SurpriseDetector(brain, threshold=0.5, on_surprise=surprises.append)
        s = detector.observe("<cpu --> [pegged]>. :|:")  # it IS pegged now
    assert s > 0.5, s
    assert len(surprises) == 1 and surprises[0].term == "<cpu --> [pegged]>"


def test_expected_event_no_surprise() -> None:
    surprises: list = []
    with Brain(cycles_per_step=50) as brain:
        brain.add_belief("<cpu --> [normal]>. {1.0 0.9}")  # prior: usually normal
        detector = SurpriseDetector(brain, threshold=0.5, on_surprise=surprises.append)
        s = detector.observe("<cpu --> [normal]>. :|:")  # normal again -> expected
    assert s <= 0.5 and surprises == []


def test_no_prior_is_not_surprising_by_default() -> None:
    surprises: list = []
    with Brain(cycles_per_step=50) as brain:
        detector = SurpriseDetector(brain, threshold=0.5, on_surprise=surprises.append)
        s = detector.observe("<cpu --> [pegged]>. :|:")  # nothing known
    assert s == 0.0 and surprises == []


def test_confidence_floor_blocks_weak_baseline() -> None:
    # Epistemic burn-in: a large divergence against a LOW-confidence baseline must NOT fire.
    fired: list = []
    with Brain(cycles_per_step=50) as brain:
        brain.add_belief("<cpu --> [pegged]>. {0.0 0.30}")  # weak baseline (little evidence)
        det = SurpriseDetector(brain, threshold=0.5, on_surprise=fired.append, min_confidence=0.85)
        s = det.observe("<cpu --> [pegged]>. :|:")
    assert s > 0.5, s          # divergence is large...
    assert fired == []          # ...but baseline isn't trusted yet -> silent (never cry wolf on Day 1)


def test_confidence_floor_allows_confident_baseline() -> None:
    # Same divergence, but a high-confidence baseline (>= 0.85 floor) -> the gate opens.
    fired: list = []
    with Brain(cycles_per_step=50) as brain:
        brain.add_belief("<cpu --> [pegged]>. {0.0 0.90}")
        det = SurpriseDetector(brain, threshold=0.5, on_surprise=fired.append, min_confidence=0.85)
        s = det.observe("<cpu --> [pegged]>. :|:")
    assert s > 0.5 and len(fired) == 1


if __name__ == "__main__":
    test_expectation_formula()
    test_strong_prior_then_anomaly_trips()
    test_expected_event_no_surprise()
    test_no_prior_is_not_surprising_by_default()
    test_confidence_floor_blocks_weak_baseline()
    test_confidence_floor_allows_confident_baseline()
    print("sentinel/test_surprise: OK")
