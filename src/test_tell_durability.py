"""Jarvis.tell durability: a raw-Narsese fact injected via `tell` must survive a restart, exactly
like `learn` — committed to the SQLite L2 (with empty English) before/with hitting the ONA L1 cache.
"""
import os
import tempfile

from brain import Brain
from jarvis import Jarvis
from language import Translator
from memory import MemoryStore, statement_term


class _NoLLM:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return "[]"


def test_tell_persists_across_restart() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    term = statement_term("<cpu --> [pegged]>. {0.0 0.9}")
    try:
        with Brain(cycles_per_step=20) as brain:
            jarvis = Jarvis(Translator(_NoLLM()), MemoryStore(db), brain)
            assert jarvis.tell("<cpu --> [pegged]>. {0.0 0.9}") is True

        # "Restart": a brand-new store on the same file must still hold the fact, English empty.
        reopened = MemoryStore(db)
        assert reopened.count() >= 1, "tell did not persist to L2"
        fact = reopened.get(term)
        assert fact is not None and fact.frequency == 0.0 and fact.confidence == 0.9, fact
        assert (fact.english or "") == "", f"expected empty English, got {fact.english!r}"
    finally:
        os.path.exists(db) and os.remove(db)


if __name__ == "__main__":
    test_tell_persists_across_restart()
    print("test_tell_durability: OK")
