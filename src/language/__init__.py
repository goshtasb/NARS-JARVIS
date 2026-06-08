"""language — the LLM channel: natural language <-> Narsese (PRD C1).

Local-first (NFR-1/2): wired strictly to llama.cpp with GBNF-constrained generation and
local embeddings for grounding. The pure layers (schema / compiler / ground / translator
wiring) need no model and are unit-tested; the `LocalLLM` / `LocalEmbedder` shells require
local GGUF models.

Public interface (ADR-001: a Python module's surface is its `__init__.py` + `__all__`).
"""
from .compiler import claims_to_narsese, to_narsese
from .embed import LocalEmbedder
from .extract import (
    REMEMBER_TAG,
    filter_known,
    filter_semantic,
    memory_acknowledgment,
    split_memory_directives,
    strip_acknowledgment,
)
from .gate import (
    L0,
    THRESHOLD_ACCEPT,
    THRESHOLD_REJECT,
    Decision,
    GateResult,
    IngestionGate,
    L0Result,
    back_render,
    is_fused,
    l1_band,
    stem,
    validate_l0,
)
from .ground import DEFAULT_THRESHOLD, cosine_similarity, nearest_atom, resolve_atom
from .llm import LocalLLM
from .schema import Claim, PropertyClaim, RelationClaim, parse_claims
from .translator import (
    DEFAULT_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    TranslationResult,
    Translator,
)
from .voice import (
    UNKNOWN_ANSWER,
    VOICE_SYSTEM_PROMPT,
    Band,
    Polarity,
    Verdict,
    Voice,
    assess,
    deterministic_answer,
    sanitize_voice,
)

__all__ = [
    "Claim",
    "RelationClaim",
    "PropertyClaim",
    "parse_claims",
    "to_narsese",
    "claims_to_narsese",
    "REMEMBER_TAG",
    "split_memory_directives",
    "memory_acknowledgment",
    "strip_acknowledgment",
    "filter_known",
    "filter_semantic",
    "L0",
    "L0Result",
    "validate_l0",
    "stem",
    "is_fused",
    "back_render",
    "l1_band",
    "Decision",
    "GateResult",
    "IngestionGate",
    "THRESHOLD_ACCEPT",
    "THRESHOLD_REJECT",
    "cosine_similarity",
    "nearest_atom",
    "resolve_atom",
    "DEFAULT_THRESHOLD",
    "LocalLLM",
    "LocalEmbedder",
    "Translator",
    "TranslationResult",
    "DEFAULT_SYSTEM_PROMPT",
    "QUESTION_SYSTEM_PROMPT",
    "Voice",
    "Verdict",
    "Polarity",
    "Band",
    "assess",
    "deterministic_answer",
    "sanitize_voice",
    "UNKNOWN_ANSWER",
    "VOICE_SYSTEM_PROMPT",
]
