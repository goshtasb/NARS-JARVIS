"""language — the LLM channel: natural language <-> Narsese (PRD C1).

Local-first (NFR-1/2): wired strictly to llama.cpp with GBNF-constrained generation and
local embeddings for grounding. The pure layers (schema / compiler / ground / translator
wiring) need no model and are unit-tested; the `LocalLLM` / `LocalEmbedder` shells require
local GGUF models.

Public interface (ADR-001: a Python module's surface is its `__init__.py` + `__all__`).
"""
from .compiler import claims_to_narsese, to_narsese
from .embed import LocalEmbedder
from .ground import DEFAULT_THRESHOLD, cosine_similarity, nearest_atom, resolve_atom
from .llm import LocalLLM
from .schema import Claim, PropertyClaim, RelationClaim, parse_claims
from .translator import DEFAULT_SYSTEM_PROMPT, TranslationResult, Translator

__all__ = [
    "Claim",
    "RelationClaim",
    "PropertyClaim",
    "parse_claims",
    "to_narsese",
    "claims_to_narsese",
    "cosine_similarity",
    "nearest_atom",
    "resolve_atom",
    "DEFAULT_THRESHOLD",
    "LocalLLM",
    "LocalEmbedder",
    "Translator",
    "TranslationResult",
    "DEFAULT_SYSTEM_PROMPT",
]
