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


def test_converse_injects_learned_habits() -> None:
    # ADR-012: a habits_provider's block is injected so the LLM respects learned preferences.
    asst = _RememberLLM("Sure.")
    habits = ("Learned habits (how the user prefers JARVIS to operate — respect these):\n"
              "- You've told JARVIS NOT to auto-hide developer apps.")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   habits_provider=lambda: habits)
        j.converse("Can you hide my IDE when I'm distracted?")
        assert "NOT to auto-hide developer apps" in asst.last_user      # habit injected


def test_converse_does_not_resave_injected_habit() -> None:
    # Habits are sourced from sentinel_beliefs, not memory — the model must not re-save them as
    # durable facts (the live ADR-012 echo bug). Habit lines are in the echo-guard `known` set.
    habits = ("Learned habits (how the user prefers JARVIS to operate — respect these):\n"
              "- When you're fragmenting between apps, you've authorized JARVIS to automatically hide chat apps.")
    asst = _RememberLLM("Sure.\n[[REMEMBER: you've authorized JARVIS to automatically hide chat apps]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   habits_provider=lambda: habits)
        out = j.converse("can you hide my chat apps?")
        assert store.memories_for_recall() == [], store.memories_for_recall()   # habit not re-saved
        assert "(Saved:" not in out, out


def test_converse_no_habits_block_when_empty() -> None:
    asst = _RememberLLM("Hi.")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   habits_provider=lambda: "")               # no confident habits
        j.converse("hello")
        assert "Learned habits" not in asst.last_user        # absent when empty


def test_converse_grounds_memory_against_denied_habit() -> None:
    # ADR-013 split-brain fix: a casual request to auto-hide a DENIED category must NOT be saved as a
    # memory; the deterministic layer owns the reply with the authoritative habit state.
    denied_dev = [("<distracted_hide_dev --> [approved]>", 0.0, 0.9)]
    asst = _RememberLLM("Sure!\n[[REMEMBER: the user wants JARVIS to auto-hide developer tools when distracted]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   sentinel_beliefs_provider=lambda: denied_dev)
        out = j.converse("please auto-hide my IDE when I'm distracted")
        assert store.memories_for_recall() == [], store.memories_for_recall()   # not saved
        assert "currently disabled" in out and "developer apps" in out          # authoritative notice
        assert "Sure!" not in out                                               # hallucinated agreement suppressed


def test_converse_no_governing_habit_saves_normally() -> None:
    asst = _RememberLLM("Noted.\n[[REMEMBER: the user wants JARVIS to auto-hide developer tools when distracted]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   sentinel_beliefs_provider=lambda: [])              # no habits at all
        j.converse("please auto-hide my IDE when I'm distracted")
        assert any("auto-hide developer tools" in m for m in store.memories_for_recall())  # saved


def test_converse_unrelated_fact_not_grounded() -> None:
    denied_dev = [("<distracted_hide_dev --> [approved]>", 0.0, 0.9)]
    asst = _RememberLLM("Nice.\n[[REMEMBER: the user's name is Ashkan]]")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   sentinel_beliefs_provider=lambda: denied_dev)
        j.converse("my name is Ashkan")
        assert "the user's name is Ashkan" in store.memories_for_recall()   # unrelated -> saved


def test_converse_output_grounding_corrects_hallucination() -> None:
    # ADR-014: the LLM contradicts a held self-fact -> hallucination suppressed, visible correction.
    asst = _RememberLLM("You live in London.")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.upsert("<user --> [in_los_angeles]>", 1.0, 0.9, english="the user lives in Los Angeles")
        j = Jarvis(Translator(asst), store, brain, assistant=asst)
        out = j.converse("Where do I live?")
        assert "London" not in out, out                      # hallucination suppressed
        assert "Correction" in out and "los angeles" in out.lower()


def test_converse_output_grounding_leaves_unrelated_answers() -> None:
    asst = _RememberLLM("Paris is the capital of France.")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        store.upsert("<user --> [in_los_angeles]>", 1.0, 0.9, english="the user lives in Los Angeles")
        j = Jarvis(Translator(asst), store, brain, assistant=asst)
        out = j.converse("What is the capital of France?")
        assert out == "Paris is the capital of France."      # untouched (no same-slot claim)


def test_converse_output_grounding_skipped_without_self_facts() -> None:
    asst = _RememberLLM("You live in London.")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst)   # no held facts
        out = j.converse("Where do I live?")
        assert out == "You live in London."                  # pre-filter skip -> untouched


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


class _FakeRunner:
    """Stub action runner: records perform() calls and returns a canned result, so converse's action
    routing is tested without touching the OS (mirrors the injected-spawn pattern in actions/). Its
    `propose` mirrors the real one: reversible actions run now -> (result, None) (ADR-019/020)."""
    def __init__(self, result: str = "(Done: mute)") -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []
    def available(self) -> list[tuple[str, str]]:
        return [("mute", "mute system audio"), ("report_system", "report system status")]
    def perform(self, name: str, arg: str = "") -> str:
        self.calls.append((name, arg))
        return self.result
    def propose(self, name: str, arg: str = ""):
        return (self.perform(name, arg), None)


def test_converse_runs_do_action_and_appends_result() -> None:
    asst = _RememberLLM("Muted.\n[[DO: mute]]")
    runner = _FakeRunner("(Done: mute system audio)")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst, action_runner=runner)
        out = j.converse("mute the volume")
        assert runner.calls == [("mute", "")]                # action executed via the runner
        assert "[[DO" not in out                             # directive stripped
        assert "Muted." in out and "(Done: mute system audio)" in out  # prose + result both shown


def test_converse_action_prompt_is_injected() -> None:
    asst = _RememberLLM("ok")
    runner = _FakeRunner()
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst, action_runner=runner)
        j.converse("hello")
        # The system prompt carries the action list; the stub records only the user msg, so assert via
        # a second converse with a [[DO]] reply that the runner is actually reachable (above test) and
        # here just that no action ran on small talk.
        assert runner.calls == []                            # no [[DO]] in the reply -> nothing run


def test_converse_directive_only_action_reply_returns_result() -> None:
    # The 7B sometimes emits ONLY the [[DO]] tag (no prose) — must return the result, not fall back.
    asst = _RememberLLM("[[DO: report_system]]")
    runner = _FakeRunner("System report:\n- CPU: 12%")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst, action_runner=runner)
        out = j.converse("what's my cpu?")
        assert out == "System report:\n- CPU: 12%"           # the result IS the reply
        assert runner.calls == [("report_system", "")]


def test_converse_unknown_action_is_safe_no_crash() -> None:
    # An invalid [[DO]] must be handled by the runner (returns a refusal) and never crash converse.
    asst = _RememberLLM("Sure.\n[[DO: nuke_everything]]")
    runner = _FakeRunner("I don't know how to do that (nuke_everything).")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst, action_runner=runner)
        out = j.converse("destroy the system")
        assert "Sure." in out and "don't know how" in out
        assert runner.calls == [("nuke_everything", "")]     # routed to the runner, which refuses


def test_converse_no_runner_ignores_do_tag() -> None:
    # With no action runner wired (tests/offline), a [[DO]] tag is simply stripped — no action, no crash.
    asst = _RememberLLM("Muted.\n[[DO: mute]]")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst)   # no action_runner
        out = j.converse("mute the volume")
        assert out == "Muted." and "[[DO" not in out


def test_converse_action_coexists_with_remember() -> None:
    asst = _RememberLLM("Opening Chrome.\n[[DO: open_app: Google Chrome]]\n[[REMEMBER: the user uses Chrome]]")
    runner = _FakeRunner("(Done: open an application by name — Google Chrome)")
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        j = Jarvis(Translator(asst), store, brain, assistant=asst, embedder=_FakeEmbedder(),
                   action_runner=runner)
        out = j.converse("open chrome")
        assert runner.calls == [("open_app", "Google Chrome")]          # action ran with its arg
        assert "the user uses Chrome" in store.memories_for_recall()    # memory saved
        assert "Opening Chrome." in out and "(Done:" in out and "(Saved:" in out


class _DestructiveRunner:
    """Runner whose `propose` returns a ConsentSpec for a destructive action (ADR-020), so converse's
    consent routing is testable without the OS."""
    def available(self): return [("empty_trash", "empty the Trash")]
    def perform(self, name, arg=""): return "(Done)"
    def propose(self, name, arg=""):
        from actions.run import ConsentSpec
        return (None, ConsentSpec(label="empty the Trash", on_approve=lambda: "(Done: emptied)"))


def test_converse_destructive_action_routes_to_consent() -> None:
    opened: list[tuple[str, object]] = []
    asst = _RememberLLM("Sure.\n[[DO: empty_trash]]")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   action_runner=_DestructiveRunner(),
                   consent_opener=lambda label, on_approve: opened.append((label, on_approve)) or 7)
        out = j.converse("empty my trash")
        assert len(opened) == 1 and opened[0][0] == "empty the Trash"   # consent opened, not executed
        assert "Awaiting your approval" in out and "empty the Trash" in out


def test_converse_destructive_action_refused_without_consent_channel() -> None:
    # No consent_opener wired -> a destructive action must be safely refused, never run unconfirmed.
    asst = _RememberLLM("Sure.\n[[DO: empty_trash]]")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   action_runner=_DestructiveRunner())          # no consent_opener
        out = j.converse("empty my trash")
        assert "needs confirmation" in out.lower()


def test_converse_injects_ax_dom() -> None:
    # ADR-021: when an ax_provider supplies a focused-window DOM, it's injected into the prompt.
    asst = _RememberLLM("ok")
    dom = ("On-screen controls (focused window — you may act on these):\n"
           "[sld_1] AXSlider \"Brightness\" = 0.6")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   ax_provider=lambda: dom)
        j.converse("how bright is my screen?")
        assert "[sld_1] AXSlider \"Brightness\"" in asst.last_user      # AX DOM injected


def test_converse_routes_ax_verb_to_dispatch() -> None:
    # An ax verb [[DO:]] must route to ax_dispatch (not the action runner / safespawn).
    calls: list[tuple[str, str]] = []
    asst = _RememberLLM("On it.\n[[DO: ax_set_value: sld_1 45]]")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst,
                   ax_dispatch=lambda verb, arg: calls.append((verb, arg)) or "⏳ Awaiting your approval: set sld_1 to 45")
        out = j.converse("set brightness to 45%")
        assert calls == [("ax_set_value", "sld_1 45")]
        assert "Awaiting your approval" in out


def test_converse_ax_verb_without_dispatch_is_safe() -> None:
    asst = _RememberLLM("On it.\n[[DO: ax_press: btn_1]]")
    with Brain(cycles_per_step=50) as brain:
        j = Jarvis(Translator(asst), MemoryStore(), brain, assistant=asst)   # no ax_dispatch
        out = j.converse("click it")
        assert "can't control on-screen elements" in out and "On it." in out


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
    test_converse_injects_learned_habits()
    test_converse_does_not_resave_injected_habit()
    test_converse_no_habits_block_when_empty()
    test_converse_grounds_memory_against_denied_habit()
    test_converse_no_governing_habit_saves_normally()
    test_converse_unrelated_fact_not_grounded()
    test_converse_falls_back_to_grounded_without_a_model()
    test_converse_trace_dedups_and_renders_uniformly()
    test_converse_unknown_is_admitted_not_invented()
    test_converse_unreadable_question()
    test_converse_formatter_hallucination_is_suppressed()
    test_converse_runs_do_action_and_appends_result()
    test_converse_action_prompt_is_injected()
    test_converse_directive_only_action_reply_returns_result()
    test_converse_unknown_action_is_safe_no_crash()
    test_converse_no_runner_ignores_do_tag()
    test_converse_action_coexists_with_remember()
    test_converse_destructive_action_routes_to_consent()
    test_converse_destructive_action_refused_without_consent_channel()
    test_converse_injects_ax_dom()
    test_converse_routes_ax_verb_to_dispatch()
    test_converse_ax_verb_without_dispatch_is_safe()
    print("test_converse: OK")
