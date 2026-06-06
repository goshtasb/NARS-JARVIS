"""Capstone M1 integration: the C2 contradiction guard wired into the learn loop.

Models the PRD's motivating case (penicillin allergy). With a deterministic fake LLM, proves:
learn a safety constraint -> learn its OPPOSITE -> the guard flags it with BOTH evidence trails,
the commit is DEFERRED to the human, and the protected L1/L2 state is never overwritten.
No model needed (the fake stands in for the GBNF-constrained LLM).
"""
from brain import Brain
from contradiction import ContradictionGuard
from jarvis import Jarvis
from language import Translator
from memory import MemoryStore


class FactsFake:
    """Deterministic stand-in for the GBNF-constrained local LLM."""

    _TABLE = {
        # "self is NOT penicillin_safe"  (penicillin is unsafe)
        "Penicillin is unsafe for me.": '[{"type":"NegatedPropertyClaim","subject":"self","value":"penicillin safe"}]',
        # "self IS penicillin_safe"      (penicillin is safe) -- the contradiction
        "Penicillin is safe for me.": '[{"type":"PropertyClaim","subject":"self","value":"penicillin safe"}]',
    }

    def generate(self, system_prompt: str, sentence: str) -> str:
        return self._TABLE[sentence]


def test_m1_contradiction_defers_commit() -> None:
    store = MemoryStore()
    conflicts: list = []
    with Brain(cycles_per_step=50) as brain:
        guard = ContradictionGuard(brain, store, on_conflict=conflicts.append)
        jarvis = Jarvis(Translator(FactsFake()), store, brain, guard=guard)

        # 1. Learn the safety constraint: penicillin is UNSAFE (self is NOT penicillin_safe).
        committed = jarvis.learn("Penicillin is unsafe for me.")
        assert committed == ["<self --> [penicillin_safe]>. {0.0 0.9}"], committed
        store.pin("<self --> [penicillin_safe]>")  # core fact, pinned
        assert store.get("<self --> [penicillin_safe]>").frequency == 0.0

        # 2. The LLM now asserts the OPPOSITE (safe). Must be flagged, NOT committed.
        committed2 = jarvis.learn("Penicillin is safe for me.")
        assert committed2 == [], "the contradicting claim must NOT be committed"

    # Surfaced to the human exactly once, with BOTH evidence trails:
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.term == "<self --> [penicillin_safe]>"
    assert conflict.incoming.frequency == 1.0  # incoming claim: penicillin is safe
    assert conflict.existing.truth is not None and conflict.existing.truth.frequency == 0.0  # held: unsafe
    assert conflict.existing.stamp, "existing side must carry an auditable evidence stamp"

    # The protected state survived — the constraint still holds FALSE in L2:
    assert store.get("<self --> [penicillin_safe]>").frequency == 0.0


if __name__ == "__main__":
    test_m1_contradiction_defers_commit()
    print("test_m1: OK (contradiction flagged with dual evidence; commit deferred; state protected)")
