"""NARS-JARVIS application orchestrator — composes brain (L1) + language + memory (L2).

The M0 capability-C1 loop: `learn(english)` writes through to L2 and feeds L1; `ask(question)`
answers from L1 with an evidence trail, and on a cache miss reloads from L2 and retries.
Composes domains via their public interfaces only (ADR-001). Imperative Shell (S-02).
"""
from __future__ import annotations

from brain import Brain
from contradiction import ContradictionGuard
from language import Translator
from memory import MemoryStore, observe, reload_into_brain, statement_term, statement_truth


class Jarvis:
    def __init__(self, translator: Translator, store: MemoryStore, brain: Brain,
                 guard: ContradictionGuard | None = None) -> None:
        self._translator = translator
        self._store = store
        self._brain = brain
        self._guard = guard

    def learn(self, sentence: str) -> list[str]:
        """English -> Narsese; for each statement run the C2 pre-commit check, then write through
        to L2 and feed L1. A contradicting statement is surfaced to the human (via the guard's
        hook) and its commit is DEFERRED — never written. Returns the committed statements.
        """
        result = self._translator.translate(sentence)
        if not result.ok:
            return []
        committed: list[str] = []
        output: list[str] = []
        for statement in result.narsese:
            if self._guard is not None and self._guard.check(statement) is not None:
                continue  # contradiction flagged to human; defer commit, keep L1/L2 protected
            term = statement_term(statement)
            self._store.upsert(term, *statement_truth(statement), english=sentence)  # write-through
            output += self._brain.add_belief(statement)  # feed L1 + run inference
            committed.append(statement)
        observe(self._store, output)  # persist truths ONA revised/derived this step
        return committed

    def ask(self, question: str):
        """Answer from L1; on a cache miss, repopulate L1 from L2 and retry once."""
        answer = self._brain.ask(question)
        if answer is None:
            reload_into_brain(self._store, self._brain)
            answer = self._brain.ask(question)
        return answer
