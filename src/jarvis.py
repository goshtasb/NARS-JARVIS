"""NARS-JARVIS application orchestrator — composes brain (L1) + language + memory (L2).

The M0 capability-C1 loop: `learn(english)` writes through to L2 and feeds L1; `ask(question)`
answers from L1 with an evidence trail, and on a cache miss reloads from L2 and retries.
Composes domains via their public interfaces only (ADR-001). Imperative Shell (S-02).
"""
from __future__ import annotations

from brain import Brain, canonical_input, input_accepted
from contradiction import ContradictionGuard
from execution import DecisionStats, Executor, decide
from language import Translator
from memory import (
    MemoryStore,
    is_valid_belief,
    observe,
    reload_into_brain,
    statement_term,
    statement_truth,
)


class InvalidNarseseError(ValueError):
    """A `tell` statement is not a well-formed Narsese belief, or ONA rejected it on parse."""


class Jarvis:
    def __init__(self, translator: Translator, store: MemoryStore, brain: Brain,
                 guard: ContradictionGuard | None = None,
                 executor: Executor | None = None) -> None:
        self._translator = translator
        self._store = store
        self._brain = brain
        self._guard = guard
        self._executor = executor  # None => orchestrator stays learn/ask only (no execution path)

    def learn(self, sentence: str) -> list[str]:
        """English -> Narsese; for each statement run the C2 pre-commit check, then feed L1 and
        commit ONA's CANONICAL form to L2 — the SAME normalization path as `tell`, so every
        ingestion route stores exactly what the engine heard. A contradicting statement is surfaced
        to the human (via the guard) and DEFERRED — never written. Returns the committed statements.
        """
        result = self._translator.translate(sentence)
        if not result.ok:
            return []
        committed: list[str] = []
        output: list[str] = []
        for statement in result.narsese:
            if self._guard is not None and self._guard.check(statement) is not None:
                continue  # contradiction flagged to human; defer commit, keep L1/L2 protected
            out = self._brain.add_belief(statement)  # feed L1 FIRST + run inference
            output += out
            if not input_accepted(out):
                continue  # ONA rejected on parse; do not commit (keeps L1/L2 in sync)
            term, frequency, confidence = self._canonical(out, statement)
            self._store.upsert(term, frequency, confidence, english=sentence)  # canonical, after L1 OK
            committed.append(statement)
        observe(self._store, output)  # persist truths ONA revised/derived this step
        return committed

    def _canonical(self, output: list[str], statement: str) -> tuple[str, float, float]:
        """ONA's normalized (term, freq, conf) from the 'Input:' echo, so L2 mirrors L1 exactly.
        Falls back to the raw statement's term/truth only if the echo is unexpectedly absent.
        """
        echo = canonical_input(output)
        if echo is not None and echo.term:
            if echo.truth is not None:
                return echo.term, echo.truth.frequency, echo.truth.confidence
            return echo.term, *statement_truth(statement)
        return statement_term(statement), *statement_truth(statement)

    def tell(self, statement: str) -> bool:
        """Ingest a raw Narsese belief directly (no LLM), durable like `learn` but desync-proof.

        Ingress order matters: (1) reject malformed syntax BEFORE any side effect; (2) C2 guard;
        (3) feed L1 and CONFIRM ONA accepted it (echoed 'Input:', no parse error); (4) only THEN
        write through to L2 (english=""). So a parse-rejected string never reaches the L2 system of
        record, and L1/L2 cannot desync. Returns True if committed, False if deferred by the guard.
        Raises InvalidNarseseError on malformed syntax or an ONA parse rejection.
        """
        statement = statement.strip()
        if not is_valid_belief(statement):
            raise InvalidNarseseError(
                f"not a well-formed Narsese belief: {statement!r} "
                "(expected a term + '.'  e.g.  <a --> b>.  or  <cpu --> [pegged]>. {0.0 0.9})"
            )
        if self._guard is not None and self._guard.check(statement) is not None:
            return False  # contradiction flagged to human; defer commit, keep L1/L2 protected
        output = self._brain.add_belief(statement)  # feed L1 FIRST
        if not input_accepted(output):
            raise InvalidNarseseError(f"ONA rejected the statement on parse; not committed: {statement!r}")
        # Store ONA's NORMALIZED form (same canonical capture as learn), not the raw typed string,
        # so the L2 system of record is a pristine reflection of L1 ('< A --> B > .' -> '<A --> B>').
        term, frequency, confidence = self._canonical(output, statement)
        self._store.upsert(term, frequency, confidence, english="")  # commit L2 only after L1 OK
        observe(self._store, output)  # persist any truths ONA revised/derived this step
        return True

    def ask(self, question: str):
        """Answer from L1; on a cache miss, repopulate L1 from L2 and retry once."""
        answer = self._brain.ask(question)
        if answer is None:
            reload_into_brain(self._store, self._brain)
            answer = self._brain.ask(question)
        return answer

    def act(self, op_name: str, arg_name: str, stats: DecisionStats):
        """Route a proposed action through the C4 decision gate to the wired executor.

        Returns the `Proposal` (so the caller sees the autonomy decision), or None if no executor
        is wired. The executor enforces every safety constraint — closed catalog, autonomy floors,
        network gate, live allowlist, env-filter — before anything reaches the engine.
        """
        if self._executor is None:
            return None
        proposal = decide(op_name, arg_name, stats)  # raises on an unregistered operation
        self._executor.execute(proposal)
        return proposal
