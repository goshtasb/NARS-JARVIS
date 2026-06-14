"""Phase 3a: Jarvis.explain() — the structured grounded verdict that feeds the Explainability View.
Proves the derivation premises (English + raw Narsese) are emitted as data, not flattened to a string."""
from brain import Brain
from jarvis import Jarvis
from language import Translator
from memory import MemoryStore


class _QLLM:
    """Maps any question to the claim <tim --> duck> (the question router is faked; ONA is real)."""
    def generate(self, system_prompt: str, sentence: str) -> str:
        return '[{"type":"RelationClaim","subject":"tim","verb":"is_a","object":"duck"}]'


def test_explain_returns_structured_verdict_with_premises() -> None:
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)
        assert j.tell("<tim --> duck>.") is True
        data = j.explain("is tim a duck?")
        assert data is not None
        assert data["statement"] and data["polarity"] == "YES"
        assert data["band"] in ("CONFIDENT", "LIKELY", "TENTATIVE")
        assert 0.0 <= data["confidence"] <= 1.0
        # the audit trail: premises carry BOTH the English mirror and the raw canonical Narsese
        narsese = {p["narsese"] for p in data["premises"]}
        assert "<tim --> duck>" in narsese
        assert all("english" in p and "narsese" in p for p in data["premises"])


def test_explain_returns_none_when_ona_has_no_grounded_answer() -> None:
    with Brain(cycles_per_step=20) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)   # nothing told -> no grounded answer
        assert j.explain("is tim a duck?") is None


def test_explain_returns_none_on_unreadable_question() -> None:
    class _NoClaim:
        def generate(self, system_prompt: str, sentence: str) -> str:
            return "[]"
    with Brain(cycles_per_step=20) as brain:
        j = Jarvis(Translator(_NoClaim()), MemoryStore(), brain)
        assert j.explain("?????") is None


if __name__ == "__main__":
    test_explain_returns_structured_verdict_with_premises()
    test_explain_returns_none_when_ona_has_no_grounded_answer()
    test_explain_returns_none_on_unreadable_question()
    print("test_explain: OK")
