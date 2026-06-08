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


class _RememberLLM:
    """Stub LLM whose free-form reply is configurable, so we can drive the [[REMEMBER]] path."""
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_user = ""
    def generate(self, system_prompt: str, sentence: str) -> str:
        return "[]"                                              # no claims -> ONA feed is a no-op
    def generate_text(self, system_prompt: str, user: str, max_tokens: int = 64) -> str:
        self.last_user = user
        return self.reply


def _jarvis(asst: _RememberLLM, store: MemoryStore, brain: Brain) -> Jarvis:
    return Jarvis(Translator(asst), store, brain, assistant=asst)


def test_converse_auto_saves_tagged_fact() -> None:
    asst = _RememberLLM("Nice to meet you, Ashkan!\n[[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        out = _jarvis(asst, store, brain).converse("My name is Ashkan")
        assert "[[REMEMBER" not in out, out                     # directive stripped from the reply
        assert "(Saved: the user's name is Ashkan)" in out, out  # visible confirmation
        assert "the user's name is Ashkan" in store.memories_for_recall()  # persisted


def test_converse_mixed_turn_answers_and_saves() -> None:
    asst = _RememberLLM("It's sunny, 22°C. [[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        out = _jarvis(asst, store, brain).converse("My name is Ashkan, what's the weather?")
        assert "sunny" in out and "[[REMEMBER" not in out, out  # answered AND tag stripped
        assert "the user's name is Ashkan" in store.memories_for_recall()


def test_converse_pure_question_saves_nothing() -> None:
    asst = _RememberLLM("Paris is the capital of France.")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        out = _jarvis(asst, store, brain).converse("What is the capital of France?")
        assert out == "Paris is the capital of France."         # unchanged, no ack
        assert store.memories_for_recall() == []                # nothing saved


def test_converse_save_independent_of_ona_gate() -> None:
    # A fact that the ONA claim path can't represent (a task) must still be remembered — the English
    # store is the guaranteed system of record; the ONA feed is best-effort and never blocks.
    asst = _RememberLLM("Will do. [[REMEMBER: the user wants to buy milk]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        _jarvis(asst, store, brain).converse("remember to buy milk")
        assert "the user wants to buy milk" in store.memories_for_recall()


def test_converse_saved_memory_injected_next_turn() -> None:
    asst = _RememberLLM("Hi Ashkan! [[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = _jarvis(asst, store, brain)
        j.converse("My name is Ashkan")                         # turn 1: saves
        asst.reply = "Your name is Ashkan."                     # turn 2: pure question
        j.converse("What is my name?")
        assert "the user's name is Ashkan" in asst.last_user, asst.last_user  # injected as ground truth


def test_converse_does_not_resave_injected_memory() -> None:
    # The live context-echo bug: with pre-existing memory injected, the model re-tags it verbatim on
    # an unrelated question. The hard guard must drop the echo -> no save, no "(Saved:" banner.
    asst = _RememberLLM("Paris.\n[[REMEMBER: the sky is blue]]")   # echoes injected fact on a pure Q
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.upsert("<sky --> [blue]>", 1.0, 0.9, english="the sky is blue")  # pre-existing memory
        out = _jarvis(asst, store, brain).converse("What is the capital of France?")
        assert "(Saved:" not in out, out                          # echo suppressed
        assert store.memories_for_recall() == []                  # nothing re-saved


def test_converse_echo_guard_keeps_new_fact_in_same_turn() -> None:
    asst = _RememberLLM("Noted.\n[[REMEMBER: the sky is blue]]\n[[REMEMBER: the user lives in Berlin]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.upsert("<sky --> [blue]>", 1.0, 0.9, english="the sky is blue")  # known -> must drop
        out = _jarvis(asst, store, brain).converse("Anything else?")
        assert "the user lives in Berlin" in store.memories_for_recall()   # new fact survives
        assert "the sky is blue" not in store.memories_for_recall()        # echo dropped
        assert "(Saved: the user lives in Berlin)" in out, out


class _FakeEmbedder:
    """Concept-keyed vectors: same-topic texts collide (incl. query<->memory), distinct topics don't.
    Note name values (ashkan/sam) share the 'name' vector so the echo guard sees them as one topic —
    the slot layer, not cosine, distinguishes a name *change* from a name *echo*."""
    def embed(self, text: str) -> list[float]:
        t = text.lower()
        if "ashkan" in t or "sam" in t or "name" in t:
            return [1.0, 0.0, 0.0, 0.0, 0.0]
        if "berlin" in t or "live" in t:
            return [0.0, 1.0, 0.0, 0.0, 0.0]
        if "tea" in t:
            return [0.0, 0.0, 1.0, 0.0, 0.0]
        if "vim" in t or "editor" in t:
            return [0.0, 0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 0.0, 1.0]


def test_converse_semantic_guard_drops_paraphrased_injected_memory() -> None:
    # The live bug the verbatim guard missed: injected "my name is Ashkan" re-tagged in third person.
    # With an embedder wired, the semantic guard must drop it -> no save, no banner.
    asst = _RememberLLM("32% CPU in use.\n[[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.upsert("<name --> [ashkan]>", 1.0, 0.9, english="my name is Ashkan")  # injected memory
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder())
        out = j.converse("What percentage of CPU are we using?")
        assert "(Saved:" not in out, out                       # paraphrase echo suppressed
        assert store.memories_for_recall() == []              # nothing re-saved


def test_converse_ranked_recall_uses_embedding_path() -> None:
    # The embedder-driven recall path injects the relevant memory (ranking proven in test_store).
    asst = _RememberLLM("Berlin.")
    emb = _FakeEmbedder()
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        for m in ("the user lives in Berlin", "the user likes tea", "the user uses vim"):
            store.remember(m, embedding=emb.embed(m))
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=emb)
        j.converse("Where does the user live?")
        assert "the user lives in Berlin" in asst.last_user      # relevant memory injected


def test_converse_name_change_supersedes_old() -> None:
    asst = _RememberLLM("Hi Sam!\n[[REMEMBER: the user's name is Sam]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.remember("the user's name is Ashkan", embedding=_FakeEmbedder().embed("ashkan"))
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder())
        j.converse("Actually my name is Sam")
        active = store.memories_for_recall()
        assert "the user's name is Sam" in active
        assert "the user's name is Ashkan" not in active         # superseded


def test_converse_directive_only_reply_still_persists() -> None:
    # The 7B sometimes emits ONLY the tag (no prose). That must still persist + confirm, NOT fall to
    # the grounded "I don't know" path (the live ADR-009 bug).
    asst = _RememberLLM("[[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder())
        out = j.converse("My name is Ashkan")
        assert "don't know" not in out.lower(), out             # not the grounded fallback
        assert "(Saved: the user's name is Ashkan)" in out, out
        assert "the user's name is Ashkan" in store.memories_for_recall()


def test_converse_forget_directive_soft_deletes() -> None:
    asst = _RememberLLM("Done.\n[[FORGET: the user likes tea]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.remember("the user likes tea", embedding=_FakeEmbedder().embed("tea"))
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder())
        out = j.converse("forget that I like tea")
        assert "the user likes tea" not in store.memories_for_recall()
        assert "Forgot" in out, out
        assert store.restore("the user likes tea") is True       # undoable


def test_converse_forget_missing_does_not_hit_wrong_sibling() -> None:
    # The live misfire: forgetting an already-gone fact must NOT tombstone a similar sibling.
    asst = _RememberLLM("Done.\n[[FORGET: the user likes tea]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.remember("the user likes coffee", embedding=_FakeEmbedder().embed("coffee"))  # only coffee
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder())
        j.converse("forget that I like tea")
        assert "the user likes coffee" in store.memories_for_recall()   # sibling untouched


def test_converse_injects_live_context() -> None:
    # ADR-010: a context_provider's live block is injected so the LLM can answer time/system questions.
    asst = _RememberLLM("It's 8:35 pm.")
    live = "Current context (live — answer from this; do NOT memorize it):\n- date/time: Sunday 2026-06-07 20:35 (local)"
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   context_provider=lambda: live)
        j.converse("What time is it?")
        assert "date/time: Sunday 2026-06-07 20:35" in asst.last_user      # live facts injected


def test_converse_does_not_persist_volatile_fact() -> None:
    # The original bug: a corrected time must NOT become a durable memory.
    asst = _RememberLLM("Got it.\n[[REMEMBER: the current time is 8 pm]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   context_provider=lambda: "Current context:\n- date/time: now")
        out = j.converse("It's 8pm")
        assert store.memories_for_recall() == []                          # nothing volatile saved
        assert "(Saved:" not in out, out


def test_converse_still_saves_durable_fact_with_live_context() -> None:
    asst = _RememberLLM("Nice!\n[[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   context_provider=lambda: "Current context:\n- date/time: now")
        j.converse("My name is Ashkan")
        assert "the user's name is Ashkan" in store.memories_for_recall()  # durable still saved


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
    test_converse_auto_saves_tagged_fact()
    test_converse_mixed_turn_answers_and_saves()
    test_converse_pure_question_saves_nothing()
    test_converse_save_independent_of_ona_gate()
    test_converse_saved_memory_injected_next_turn()
    test_converse_does_not_resave_injected_memory()
    test_converse_echo_guard_keeps_new_fact_in_same_turn()
    test_converse_semantic_guard_drops_paraphrased_injected_memory()
    test_converse_ranked_recall_uses_embedding_path()
    test_converse_name_change_supersedes_old()
    test_converse_directive_only_reply_still_persists()
    test_converse_forget_directive_soft_deletes()
    test_converse_forget_missing_does_not_hit_wrong_sibling()
    test_converse_injects_live_context()
    test_converse_does_not_persist_volatile_fact()
    test_converse_still_saves_durable_fact_with_live_context()
    test_converse_falls_back_to_grounded_without_a_model()
    test_converse_trace_dedups_and_renders_uniformly()
    test_converse_unknown_is_admitted_not_invented()
    test_converse_unreadable_question()
    test_converse_formatter_hallucination_is_suppressed()
    print("test_converse: OK")
