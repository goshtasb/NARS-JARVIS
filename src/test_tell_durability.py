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


if __name__ == "__main__":
    test_validator_accepts_well_formed_and_rejects_garbage()
    test_tell_persists_across_restart()
    test_malformed_tell_never_touches_l2()
    print("test_tell_durability: OK")
