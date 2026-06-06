"""Translator — orchestrates English -> claims -> grounding -> Narsese -> brain (PRD C1).

Imperative Shell (S-02): wires the injected LLM / embedder / brain; pure work is delegated to
schema/compiler/ground. Malformed or schema-violating LLM output (which GBNF should prevent,
but we defend in depth) is caught here and routed to an alert hook — the `sentinel` subscribes
to this in M2. The pipeline never crashes on bad model output; it rejects and alerts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from .compiler import claims_to_narsese
from .ground import DEFAULT_THRESHOLD, resolve_atom
from .schema import Claim, RelationClaim, parse_claims

DEFAULT_SYSTEM_PROMPT = (
    "Convert the sentence into a JSON array of claims using only RelationClaim, PropertyClaim, "
    "NegatedRelationClaim, NegatedPropertyClaim. Output JSON only."
)

# Exceptions a malformed / schema-violating model response can raise downstream.
_PARSE_ERRORS = (ValueError, KeyError, TypeError, json.JSONDecodeError)


class ClaimSource(Protocol):
    def generate(self, system_prompt: str, sentence: str) -> str: ...


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class TranslationResult:
    ok: bool
    narsese: list[str]
    error: str | None = None


class Translator:
    """Wires the language pipeline. All collaborators are injected (testable with fakes)."""

    def __init__(
        self,
        llm: ClaimSource,
        embedder: Embedder | None = None,
        brain: object | None = None,
        on_reject: Callable[[str, str], None] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._brain = brain
        self._on_reject = on_reject or (lambda sentence, error: None)
        self._threshold = threshold
        self._atoms: dict[str, list[float]] = {}  # grounding state: atom -> embedding

    def translate(self, sentence: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> TranslationResult:
        try:
            raw = self._llm.generate(system_prompt, sentence)
            claims = parse_claims(raw)
        except _PARSE_ERRORS as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._on_reject(sentence, error)  # alert the sentinel; do NOT crash the pipeline
            return TranslationResult(False, [], error)
        if self._embedder is not None:
            claims = [self._ground(c) for c in claims]
        narsese = claims_to_narsese(claims)
        if self._brain is not None:
            for statement in narsese:
                self._brain.add_belief(statement)  # type: ignore[attr-defined]
        return TranslationResult(True, narsese)

    def _ground_atom(self, name: str) -> str:
        vec = self._embedder.embed(name)  # type: ignore[union-attr]
        atom, created = resolve_atom(name, vec, self._atoms, self._threshold)
        if created:
            self._atoms[atom] = vec
        return atom

    def _ground(self, claim: Claim) -> Claim:
        if isinstance(claim, RelationClaim):
            return replace(
                claim,
                subject=self._ground_atom(claim.subject),
                verb=self._ground_atom(claim.verb),
                object=self._ground_atom(claim.object),
            )
        return replace(claim, subject=self._ground_atom(claim.subject), value=self._ground_atom(claim.value))
