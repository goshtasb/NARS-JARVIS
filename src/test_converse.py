"""End-to-end conversational ask: English question -> grounded ONA query -> English answer,
with the zero-hallucination guarantees (cited evidence, template-authoritative voice)."""
from brain import Brain
from jarvis import Jarvis
from language import Translator, Voice
from memory import MemoryStore


class _QLLM:
    """Deterministic question-translator stand-in for the GBNF-constrained model."""
    def generate(self, system_prompt: str, sentence: str) -> str:
        s = sentence.lower()
        if "bird" in s:
            return '[{"type":"RelationClaim","subject":"Tim","verb":"is_a","object":"bird"}]'
        if "reptile" in s:
            return '[{"type":"RelationClaim","subject":"Tim","verb":"is_a","object":"reptile"}]'
        return "[]"


def _teach(j: Jarvis) -> None:
    j.tell("<tim --> duck>.")
    j.tell("<duck --> bird>.")


def test_converse_yes_with_cited_evidence() -> None:
    with Brain(cycles_per_step=200) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)   # voice defaults to template-only
        _teach(j)
        out = j.converse("Is Tim a bird?")
        assert "Tim is a bird" in out, out                       # derived, voiced affirmatively
        assert "confidence" in out and "based on" in out, out    # truth + evidence trail present
        assert "duck" in out, out                                # cites the real premise chain


def test_converse_unknown_is_admitted_not_invented() -> None:
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)
        _teach(j)
        out = j.converse("Is Tim a reptile?")                    # never taught anything reptilian
        assert "don't know" in out.lower(), out


def test_converse_unreadable_question() -> None:
    with Brain(cycles_per_step=20) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)
        out = j.converse("asdfghjkl")                            # _QLLM yields no claim
        assert "couldn't read" in out.lower(), out


def test_converse_formatter_hallucination_is_suppressed() -> None:
    class _Bad:
        def generate(self, s, x):
            return '[{"type":"RelationClaim","subject":"Tim","verb":"is_a","object":"bird"}]'
        def generate_text(self, s, u):
            return "Tim is actually a rare penguin from Antarctica."  # invents content
    with Brain(cycles_per_step=200) as brain:
        j = Jarvis(Translator(_Bad()), MemoryStore(), brain, voice=Voice(formatter=_Bad()))
        _teach(j)
        out = j.converse("Is Tim a bird?")
        assert "penguin" not in out.lower() and "antarctica" not in out.lower(), out
        assert "Tim is a bird" in out, out                       # fell back to the safe template


if __name__ == "__main__":
    test_converse_yes_with_cited_evidence()
    test_converse_unknown_is_admitted_not_invented()
    test_converse_unreadable_question()
    test_converse_formatter_hallucination_is_suppressed()
    print("test_converse: OK")
