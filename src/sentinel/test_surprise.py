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


if __name__ == "__main__":
    test_expectation_formula()
    test_strong_prior_then_anomaly_trips()
    test_expected_event_no_surprise()
    test_no_prior_is_not_surprising_by_default()
    print("sentinel/test_surprise: OK")
