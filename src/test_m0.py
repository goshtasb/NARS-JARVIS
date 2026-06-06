"""Capstone M0 integration: the full two-tier loop with a deterministic fake LLM.

Proves capability C1 end to end: learn English -> L2 + L1 -> ask (answered with evidence);
then a FRESH L1 (simulating ONA eviction) -> ask -> cache-miss reload from L2 -> still answered.
This is the M0 lock-down test. No model needed (the fake stands in for the GBNF-constrained LLM).
"""
from brain import Brain
from jarvis import Jarvis
from language import Translator
from memory import MemoryStore


class FactsFake:
    """Deterministic stand-in for the GBNF-constrained local LLM."""

    _TABLE = {
        "Tim is a duck.": '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"}]',
        "Ducks are birds.": '[{"type":"RelationClaim","subject":"duck","verb":"IsA","object":"bird"}]',
    }

    def generate(self, system_prompt: str, sentence: str) -> str:
        return self._TABLE[sentence]


def test_m0_two_tier_loop() -> None:
    store = MemoryStore()
    # 1. Learn in English; ask; answered from L1 with an evidence trail.
    with Brain(cycles_per_step=100) as brain:
        jarvis = Jarvis(Translator(FactsFake()), store, brain)
        jarvis.learn("Tim is a duck.")
        jarvis.learn("Ducks are birds.")
        answer = jarvis.ask("<tim --> bird>?")
        assert answer is not None and answer.term == "<tim --> bird>", f"L1: {answer}"
        assert answer.stamp, "expected an evidence trail"

    # 2. L2 holds the facts durably; simulate eviction with a brand-new empty ONA.
    assert store.count() >= 2
    with Brain(cycles_per_step=100) as fresh_brain:
        jarvis2 = Jarvis(Translator(FactsFake()), store, fresh_brain)
        recovered = jarvis2.ask("<tim --> bird>?")  # empty L1 -> cache-miss -> reload from L2
        assert recovered is not None and recovered.term == "<tim --> bird>", f"recovered: {recovered}"


if __name__ == "__main__":
    test_m0_two_tier_loop()
    print("test_m0: OK (two-tier learn/ask + cache-miss recovery)")
