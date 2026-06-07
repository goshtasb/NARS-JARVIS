"""Intervention rendering + steadiness mapping — deterministic, closed-vocabulary, no LLM."""
from sentinel.intervention import intervention_prompt, is_steady, steadiness_belief


def test_steadiness_mapping() -> None:
    assert is_steady("focused") and is_steady("light")
    assert not is_steady("fragmented") and not is_steady("thrashing")
    # steady -> freq 1 baseline; unsteady -> freq 0. The Sentinel learns 'usually steady'.
    assert steadiness_belief("focused") == "<attention --> [steady]>. {1.0 0.9}"
    assert steadiness_belief("thrashing") == "<attention --> [steady]>. {0.0 0.9}"


def test_intervention_prompt_is_closed_vocab() -> None:
    p = intervention_prompt("thrashing", ["comms", "media"], minutes=25)
    assert "thrashing" in p and "comms, media" in p and "25m" in p and "[y/n]" in p
    # no categories -> generic but still well-formed (never an empty/None splat)
    assert "distraction" in intervention_prompt("fragmented", [])


if __name__ == "__main__":
    test_steadiness_mapping()
    test_intervention_prompt_is_closed_vocab()
    print("sentinel/test_intervention: OK")
