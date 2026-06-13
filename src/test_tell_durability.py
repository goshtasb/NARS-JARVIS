"""Jarvis.tell durability + ingress validation.

`tell` must (a) persist a valid raw-Narsese fact to the SQLite L2 so it survives a restart, and
(b) REJECT malformed input at ingress WITHOUT touching L2 — a parse-rejected string must never
desync the two tiers. ONA is the authority: L2 commits only after a confirmed L1 'Input:' echo.
"""
import os
import tempfile

from brain import Brain
from jarvis import InvalidNarseseError, Jarvis
from language import Translator
from memory import MemoryStore, is_valid_belief, statement_term


class _NoLLM:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return "[]"


class _SpyBrain:
    """Wraps a real Brain and records every add_belief() call, to PROVE the passive write path
    bypasses ONA L1 entirely (and that the trusted path still feeds it)."""
    def __init__(self, inner: Brain) -> None:
        self._inner = inner
        self.add_belief_calls: list[str] = []

    def add_belief(self, statement: str):
        self.add_belief_calls.append(statement)
        return self._inner.add_belief(statement)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _source_of(store: MemoryStore, term: str):
    return store._db.execute("SELECT source FROM facts WHERE narsese=?", (term,)).fetchone()[0]


def test_validator_accepts_well_formed_and_rejects_garbage() -> None:
    for good in ("<a --> b>.", "<cpu --> [pegged]>. {0.0 0.9}", "<cpu --> [pegged]>. :|:", "cat."):
        assert is_valid_belief(good), good
    for bad in ("garbage(((", "<a --> b>?", "<a --> b>!", "<a --> b", "", "{0.5 0.9}",
                "<a --> b>. {1.5 0.9}"):
        assert not is_valid_belief(bad), bad


def test_tell_persists_across_restart() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<cpu --> [pegged]>. {0.0 0.9}")
    try:
        with Brain(cycles_per_step=20) as brain:
            jarvis = Jarvis(Translator(_NoLLM()), MemoryStore(db), brain)
            assert jarvis.tell("<cpu --> [pegged]>. {0.0 0.9}") is True
        reopened = MemoryStore(db)  # "restart": fresh store on the same file
        assert reopened.count() >= 1, "tell did not persist to L2"
        fact = reopened.get(term)
        assert fact is not None and fact.frequency == 0.0 and fact.confidence == 0.9, fact
        assert (fact.english or "") == "", f"expected empty English, got {fact.english!r}"
    finally:
        os.path.exists(db) and os.remove(db)


def test_tell_stores_onas_canonical_normalized_form() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        with Brain(cycles_per_step=20) as brain:
            store = MemoryStore(db)
            jarvis = Jarvis(Translator(_NoLLM()), store, brain)
            assert jarvis.tell("< A  -->  B > .") is True  # erratic-but-valid spacing
            # L2 must hold ONA's NORMALIZED term, never the raw typed string.
            assert store.get("<A --> B>") is not None, "canonical term not stored"
            assert store.get("< A  -->  B > .") is None, "raw un-normalized string leaked into L2"
            assert store.get("< A  -->  B >") is None
    finally:
        os.path.exists(db) and os.remove(db)


def test_learn_also_stores_onas_canonical_form() -> None:
    # Alignment: the LLM `learn` path stores ONA's normalization, identical to `tell`.
    class _Claims:
        def generate(self, system_prompt: str, sentence: str) -> str:
            return '[{"type":"RelationClaim","subject":"tim","verb":"IsA","object":"duck"}]'

    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        with Brain(cycles_per_step=50) as brain:
            store = MemoryStore(db)
            committed = Jarvis(Translator(_Claims()), store, brain).learn("Tim is a duck.")
            assert committed, "learn should commit the statement"
            assert store.get("<tim --> duck>") is not None, "learn did not store ONA's canonical term"
    finally:
        os.path.exists(db) and os.remove(db)


def test_malformed_tell_never_touches_l2() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        with Brain(cycles_per_step=20) as brain:
            store = MemoryStore(db)
            jarvis = Jarvis(Translator(_NoLLM()), store, brain)
            for bad in ("garbage(((", "<a --> b", "<a --> b>?"):
                try:
                    jarvis.tell(bad)
                    raise AssertionError(f"expected rejection for {bad!r}")
                except InvalidNarseseError:
                    pass
            assert store.count() == 0, "malformed tell polluted L2 (desync!)"
    finally:
        os.path.exists(db) and os.remove(db)


def test_passive_tell_bypasses_l1_and_lands_in_l2_at_the_floor() -> None:
    # v1.24.0 Step 2: source='passive' must write L2 ONLY (never ONA L1), tagged 'passive', at the
    # corroboration floor (confidence 0.5), so the firehose can't thrash the 4096-concept bag.
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<chrome --> [foreground]>.")
    try:
        with Brain(cycles_per_step=20) as brain:
            spy = _SpyBrain(brain)
            store = MemoryStore(db)
            jarvis = Jarvis(Translator(_NoLLM()), store, spy)
            assert jarvis.tell("<chrome --> [foreground]>.", source="passive") is True
            assert spy.add_belief_calls == [], "passive tell leaked into ONA L1 (add_belief was called)"
            fact = store.get(term)
            assert fact is not None, "passive tell did not persist to L2"
            assert fact.frequency == 1.0 and fact.confidence == 0.5, fact   # floored confidence
            assert _source_of(store, term) == "passive", "passive belief not tagged source='passive'"
    finally:
        os.path.exists(db) and os.remove(db)


def test_passive_tell_floors_confidence_even_if_statement_claims_higher() -> None:
    # The floor is enforced by the write path, not trusted from the input: a passive observation that
    # *claims* high confidence still enters at 0.5.
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<disk --> [busy]>. {1.0 0.9}")
    try:
        with Brain(cycles_per_step=20) as brain:
            store = MemoryStore(db)
            Jarvis(Translator(_NoLLM()), store, brain).tell("<disk --> [busy]>. {1.0 0.9}", source="passive")
            fact = store.get(term)
            assert fact is not None and fact.confidence == 0.5, f"floor not enforced: {fact}"
    finally:
        os.path.exists(db) and os.remove(db)


def test_trusted_tell_still_feeds_l1_and_leaves_source_null() -> None:
    # The default (no source) path is UNCHANGED: it feeds ONA L1 and writes source NULL (legacy/trusted).
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<a --> b>.")
    try:
        with Brain(cycles_per_step=20) as brain:
            spy = _SpyBrain(brain)
            store = MemoryStore(db)
            assert Jarvis(Translator(_NoLLM()), store, spy).tell("<a --> b>.") is True
            assert spy.add_belief_calls, "trusted tell must still feed ONA L1"
            assert _source_of(store, term) is None, "trusted tell must leave source NULL (trusted tier)"
    finally:
        os.path.exists(db) and os.remove(db)


def test_passive_observation_never_downgrades_an_existing_trusted_belief() -> None:
    # A passive re-sighting of a fact already told/trusted (source NULL) must NOT rewrite its tier to
    # 'passive' — else the decay sweep could later prune a belief the user explicitly taught.
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<sol --> blockchain>.")
    try:
        with Brain(cycles_per_step=20) as brain:
            store = MemoryStore(db)
            jarvis = Jarvis(Translator(_NoLLM()), store, brain)
            assert jarvis.tell("<sol --> blockchain>.") is True       # trusted first -> source NULL
            assert jarvis.tell("<sol --> blockchain>.", source="passive") is True  # passive re-sighting
            assert _source_of(store, term) is None, "passive re-sighting downgraded a trusted belief's tier"
    finally:
        os.path.exists(db) and os.remove(db)


if __name__ == "__main__":
    test_validator_accepts_well_formed_and_rejects_garbage()
    test_tell_persists_across_restart()
    test_tell_stores_onas_canonical_normalized_form()
    test_learn_also_stores_onas_canonical_form()
    test_malformed_tell_never_touches_l2()
    test_passive_tell_bypasses_l1_and_lands_in_l2_at_the_floor()
    test_passive_tell_floors_confidence_even_if_statement_claims_higher()
    test_trusted_tell_still_feeds_l1_and_leaves_source_null()
    test_passive_observation_never_downgrades_an_existing_trusted_belief()
    print("test_tell_durability: OK")
