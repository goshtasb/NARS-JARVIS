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


class _Assistant:
    """Stub LLM with the free-form generate_text path (the LLM-first brain, ADR-007)."""
    last_user = ""
    def generate(self, system_prompt: str, sentence: str) -> str:
        return "[]"
    def generate_text(self, system_prompt: str, user: str, max_tokens: int = 64) -> str:
        type(self).last_user = user
        return "Paris is the capital of France."


def test_converse_llm_first_answers_and_injects_memory() -> None:
    # With a model wired, the LLM answers from its OWN knowledge, and the user's taught facts are
    # injected as ground truth — the post-pivot behavior.
    asst = _Assistant()
    with Brain(cycles_per_step=50) as brain:
        st = MemoryStore()
        st.upsert("<sky --> blue>", 1.0, 0.9, english="the sky is blue")   # a taught fact
        j = Jarvis(Translator(_QLLM()), st, brain, assistant=asst)
        out = j.converse("What is the capital of France?")
        assert out == "Paris is the capital of France."                    # answered from own knowledge
        assert "the sky is blue" in _Assistant.last_user                   # memory injected as ground truth


def test_converse_falls_back_to_grounded_without_a_model() -> None:
    # No assistant wired (tests / offline) -> the legacy hallucination-proof ONA path still works.
    with Brain(cycles_per_step=200) as brain:
        j = Jarvis(Translator(_QLLM()), MemoryStore(), brain)             # no assistant
        _teach(j)
        assert "Tim is a bird" in j.converse("Is Tim a bird?")


def test_converse_trace_dedups_and_renders_uniformly() -> None:
    # The audit trail must collapse a premise ONA cites via multiple evidence ids, and render each
    # via the fallback hierarchy: English alias if the L2 store has one, else clean canonical Narsese.
    with Brain(cycles_per_step=200) as brain:
        st = MemoryStore()
        j = Jarvis(Translator(_QLLM()), st, brain)
        j.tell("<tim --> duck>.")
        j.tell("<tim --> duck>.")                                # committed twice -> 2 evidence ids
        j.tell("<duck --> bird>.")
        st.upsert("<duck --> bird>", 1.0, 0.9, english="ducks are birds")  # give it an English alias
        out = j.converse("Is Tim a bird?")
        assert out.count("<tim --> duck>") == 1, out             # deduped to a single citation
        assert "ducks are birds" in out, out                    # English alias preferred where present
        assert "Tim is a bird" in out, out


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
    test_converse_llm_first_answers_and_injects_memory()
    test_converse_falls_back_to_grounded_without_a_model()
    test_converse_trace_dedups_and_renders_uniformly()
    test_converse_unknown_is_admitted_not_invented()
    test_converse_unreadable_question()
    test_converse_formatter_hallucination_is_suppressed()
    print("test_converse: OK")
