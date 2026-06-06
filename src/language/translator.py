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

from shared import atom

from .compiler import claims_to_narsese
from .gate import stem
from .ground import DEFAULT_THRESHOLD
from .schema import Claim, RelationClaim, parse_claims

DEFAULT_SYSTEM_PROMPT = (
    "You extract logical claims from a sentence as a JSON array. Use ONLY these claim types:\n"
    "- RelationClaim {subject, verb, object}: a relation between two concepts. For 'X is a Y' or "
    "'X are Y' (category/taxonomy), set verb to \"is_a\". For other relations use the plain base "
    "verb (e.g. \"makes\", \"eats\").\n"
    "- PropertyClaim {subject, value}: 'X is ADJECTIVE' — a quality of one concept (e.g. hot, safe).\n"
    "- NegatedRelationClaim / NegatedPropertyClaim: the negated forms ('X is not Y', 'X is not ADJ').\n"
    "Normalization rules: ALWAYS convert plural nouns to SINGULAR (ducks->duck, birds->bird, "
    "dogs->dog, cats->cat); DROP articles (a, an, the); each subject/object/value is ONE concept; "
    "drop pronouns/possessives like 'for me', 'my'. Output ONLY the JSON array, no prose.\n"
    "Examples:\n"
    "Tim is a duck. => [{\"type\":\"RelationClaim\",\"subject\":\"Tim\",\"verb\":\"is_a\",\"object\":\"duck\"}]\n"
    "Ducks are birds. => [{\"type\":\"RelationClaim\",\"subject\":\"duck\",\"verb\":\"is_a\",\"object\":\"bird\"}]\n"
    "Coffee is hot. => [{\"type\":\"PropertyClaim\",\"subject\":\"coffee\",\"value\":\"hot\"}]\n"
    "Penicillin is not safe. => [{\"type\":\"NegatedPropertyClaim\",\"subject\":\"penicillin\",\"value\":\"safe\"}]"
)

# Exceptions a malformed / schema-violating model response can raise downstream.
_PARSE_ERRORS = (ValueError, KeyError, TypeError, json.JSONDecodeError)


class ClaimSource(Protocol):
    def generate(self, system_prompt: str, sentence: str) -> str: ...


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class GroundingCache(Protocol):
    """Persistent entity-resolution cache (impl: memory.SqliteGroundingStore). Structural."""
    def resolve_surface(self, surface: str) -> str | None: ...
    def nearest(self, query_vec: list[float], threshold: float) -> str | None: ...
    def add_atom(self, name: str, raw_vec: list[float]) -> None: ...
    def add_alias(self, surface: str, canonical: str) -> None: ...


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
        cache: GroundingCache | None = None,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._brain = brain
        self._on_reject = on_reject or (lambda sentence, error: None)
        self._threshold = threshold
        # Persistent grounding requires BOTH an embedder and a cache; otherwise atoms pass through
        # un-grounded (the compiler still sanitizes them at to_narsese).
        self._cache = cache

    def claims(self, sentence: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> list[Claim]:
        """English -> GROUNDED typed claims (generate + parse + ground). No compile, no brain write.

        This is the AST the ingestion gate validates BEFORE anything is committed. Raises on
        malformed model output (one of `_PARSE_ERRORS`); the caller decides how to surface it.
        """
        parsed = parse_claims(self._llm.generate(system_prompt, sentence))
        if self._embedder is not None and self._cache is not None:
            parsed = [self._ground(c) for c in parsed]
        return parsed

    def translate(self, sentence: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> TranslationResult:
        try:
            claims = self.claims(sentence, system_prompt)
        except _PARSE_ERRORS as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._on_reject(sentence, error)  # alert the sentinel; do NOT crash the pipeline
            return TranslationResult(False, [], error)
        narsese = claims_to_narsese(claims)
        if self._brain is not None:
            for statement in narsese:
                self._brain.add_belief(statement)  # type: ignore[attr-defined]
        return TranslationResult(True, narsese)

    def _ground_atom(self, name: str) -> str:
        """Resolve a raw token to its canonical atom, cheapest-first; the embedder is the LAST
        resort, invoked at most once per novel surface form, ever (then memoized as an alias).
        """
        surface = atom(name)                                  # 1. sanitize -> surface key
        hit = self._cache.resolve_surface(surface)            # 2/3. alias OR canonical exact hit
        if hit is not None:
            return hit
        stemmed = stem(surface)                               # 4. inflectional stemmer fast-path
        if stemmed != surface:
            hit = self._cache.resolve_surface(stemmed)
            if hit is not None:
                self._cache.add_alias(surface, hit)           # memoize -> free next time
                return hit
        vec = self._embedder.embed(surface)                   # 5. the ONLY paid step
        vec = vec[0] if vec and isinstance(vec[0], list) else vec
        canonical = self._cache.nearest(vec, self._threshold)
        if canonical is not None:
            if canonical != surface:
                self._cache.add_alias(surface, canonical)     # persist surface -> canonical
            return canonical
        self._cache.add_atom(surface, vec)                    # a brand-new canonical concept
        return surface

    def _ground(self, claim: Claim) -> Claim:
        if isinstance(claim, RelationClaim):
            return replace(
                claim,
                subject=self._ground_atom(claim.subject),
                verb=self._ground_atom(claim.verb),
                object=self._ground_atom(claim.object),
            )
        return replace(claim, subject=self._ground_atom(claim.subject), value=self._ground_atom(claim.value))
