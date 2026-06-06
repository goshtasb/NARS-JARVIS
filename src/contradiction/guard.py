"""Pre-commit contradiction guard (C2 / M1). Imperative Shell (S-02).

Read-before-write: query ONA non-destructively for the incoming claim, compute the polarity
conflict in Python, and surface both evidence trails — never auto-block, never pollute L1 with
the candidate. The human decides (override -> revise, or reject). See PRD C2 and ADR.

Why pre-commit (not post-commit + rollback): ONA emits no contradiction signal (Truth_Revision
silently merges) and has no un-ingest, so L1 cannot be rolled back. Checking before committing
keeps the cache sterile.
"""
from __future__ import annotations

from typing import Callable

from brain import Truth
from memory import MemoryStore, reload_into_brain, statement_term, statement_truth

from .check import DEFAULT_MIN_CONFIDENCE, Conflict, is_contradiction


class ContradictionGuard:
    def __init__(
        self,
        brain: object,
        store: MemoryStore,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        reload_limit: int = 40,
        on_conflict: Callable[[Conflict], None] | None = None,
    ) -> None:
        self._brain = brain
        self._store = store
        self._min_confidence = min_confidence
        self._reload_limit = reload_limit
        self._on_conflict = on_conflict or (lambda conflict: None)

    def check(self, statement: str) -> Conflict | None:
        """Return a Conflict (and alert the human) if `statement` contradicts committed knowledge.

        Non-destructive: warms L1 with already-committed facts from L2 and queries the term; the
        candidate statement is NOT added here.
        """
        term = statement_term(statement)
        frequency, confidence = statement_truth(statement)
        incoming = Truth(frequency, confidence)
        # Warm L1 from L2 so committed knowledge is queryable (does NOT add the candidate).
        reload_into_brain(self._store, self._brain, self._reload_limit)
        existing = self._brain.ask(term + "?")  # type: ignore[attr-defined]
        if existing is None or existing.truth is None:
            return None
        if is_contradiction(incoming, existing.truth, self._min_confidence):
            conflict = Conflict(
                term=term,
                incoming=incoming,
                incoming_statement=statement,
                existing=existing,
            )
            self._on_conflict(conflict)  # flag-to-human; no auto-block, no mutation
            return conflict
        return None
