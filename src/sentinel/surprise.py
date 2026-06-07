"""Surprise detection (C3 / M2). Imperative Shell wrapping ONA + a pure divergence calc.

Mechanism: for each sentinel event, query ONA's PRIOR expectation for that term (before
injecting), then inject the event, then compute surprise = |expectation(actual) − expectation
(prior)|. Above a configurable threshold -> a SurpriseEvent for narration.

Honest boundary (same as M1): the prior must be a belief about the SAME term (e.g. "CPU is
usually NOT pegged"). "CPU is usually normal" only implies "not pegged" with the deferred
uniqueness layer, so the prior is established per-term.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from brain import Truth
from memory import statement_term, statement_truth


def expectation(truth: Truth) -> float:
    """NAL truth expectation = conf * (freq - 0.5) + 0.5. Pure."""
    return truth.confidence * (truth.frequency - 0.5) + 0.5


@dataclass(frozen=True)
class SurpriseEvent:
    term: str
    statement: str
    prior_expectation: float | None
    actual_expectation: float
    surprise: float


class SurpriseDetector:
    """The sentinel's sink: feeds events to ONA and fires on prediction divergence."""

    def __init__(self, brain: object, threshold: float = 0.5,
                 on_surprise: Callable[[SurpriseEvent], object] | None = None,
                 no_prior_surprise: float = 0.0, min_confidence: float = 0.0) -> None:
        self._brain = brain
        self._threshold = threshold
        self._on_surprise = on_surprise or (lambda event: None)
        self._no_prior_surprise = no_prior_surprise
        # Epistemic burn-in: never fire until the PRIOR baseline has accumulated enough evidence.
        # ONA confidence c = w/(w+k), so e.g. 0.85 ~ 6 confirmations. 0.0 keeps legacy M2 behavior.
        self._min_confidence = min_confidence

    def observe(self, statement: str) -> float:
        """Sink for sentinel events. Injects into ONA and returns the computed surprise."""
        term = statement_term(statement)
        prior = self._brain.ask(term + "?")  # type: ignore[attr-defined]  # PRIOR (pre-injection)
        has_prior = prior is not None and prior.truth is not None
        prior_exp = expectation(prior.truth) if has_prior else None
        prior_conf = prior.truth.confidence if has_prior else 0.0
        self._brain.add_belief(statement)  # type: ignore[attr-defined]  # feed the event to ONA
        actual_exp = expectation(Truth(*statement_truth(statement)))
        surprise = self._no_prior_surprise if prior_exp is None else abs(actual_exp - prior_exp)
        # Gate: divergence must exceed threshold AND the baseline must be trustworthy (burn-in).
        if surprise > self._threshold and prior_conf >= self._min_confidence:
            self._on_surprise(
                SurpriseEvent(term, statement, prior_exp, actual_exp, surprise)
            )
        return surprise
