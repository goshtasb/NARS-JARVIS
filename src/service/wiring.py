"""Model wiring for the daemon — how the optional local LLM / embedder are sourced, with safe
offline fallbacks. Kept separate from the command plane (session.py) so the Session is purely
orchestration: it asks for a claim source / embedder and never knows how they were built.
"""
from __future__ import annotations

import os
import sys


class DemoClaims:
    """Tiny offline claim source so `learn` works without a model for a couple of demo sentences."""
    _T = {
        "Tim is a duck.": '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"}]',
        "Ducks are birds.": '[{"type":"RelationClaim","subject":"duck","verb":"IsA","object":"bird"}]',
    }
    def generate(self, system_prompt: str, sentence: str) -> str:
        return self._T.get(sentence, "[]")


class NoNarrationLLM:
    """No GGUF wired -> the Narrator's deterministic, action-forbidden fallback is used."""
    def generate(self, system_prompt: str, user: str) -> str:
        raise RuntimeError("no narration model")


class LazyLLM:
    """Phase 1 (memory): a lazy, evictable wrapper around the heavy LocalLLM (~4.2 GB resident).

    The model is built on the FIRST inference call (not at boot), and `evict()` drops the reference so the
    OS reclaims the weights + Metal context when the daemon goes idle. Capability-transparent: it statically
    advertises the LocalLLM inference methods, so `hasattr(brain, "generate_text")` stays honest WITHOUT
    forcing a load (the no-GGUF path uses DemoClaims and never reaches this). All real loading/inference is
    driven UNDER LocalBrain._lock, so a reload or eviction can never race an in-flight decode.
    """
    _METHODS = ("generate", "generate_json", "generate_text", "to_claims", "create_chat_completion")

    def __init__(self, factory) -> None:
        self._factory = factory                # () -> LocalLLM
        self._llm = None                       # None == evicted / never loaded

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    def _ensure(self):
        if self._llm is None:
            self._llm = self._factory()        # ~5 s cold load — happens under LocalBrain._lock
        return self._llm

    def evict(self) -> None:
        self._llm = None                       # drop the ref -> GC frees ~4.2 GB

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)         # never delegate privates (no recursion on _llm/_factory)
        if name in LazyLLM._METHODS:           # capability visible while unloaded; loads only when CALLED
            def call(*a, **k):
                return getattr(self._ensure(), name)(*a, **k)
            return call
        return getattr(self._ensure(), name)   # any other real attr -> load + delegate


def make_claim_source():
    """Returns the Multiplexer-wrapped brain (ADR-056). Default mode is **private** → it delegates
    verbatim to the local LLM, so today's behavior is unchanged until the session sets General mode
    (with a per-request key) on it. The Multiplexer is the single injection point for both brains.
    Phase 1: the LocalLLM is wrapped in LazyLLM so its ~4.2 GB only loads on first use and can be evicted
    when idle — the Multiplexer's pass-through __getattr__ exposes LazyLLM.loaded/.evict to LocalBrain."""
    if os.environ.get("NARS_JARVIS_LLM_GGUF"):
        try:
            from language import LocalLLM
            from language.multiplexer import Multiplexer
            return Multiplexer(LazyLLM(lambda: LocalLLM()))
        except Exception as exc:  # noqa: BLE001 — degrade gracefully to offline demo source
            sys.stderr.write(f"[warn] LocalLLM unavailable ({exc}); NL learning limited\n")
    return DemoClaims()


def make_embedder():
    if os.environ.get("NARS_JARVIS_EMBED_GGUF"):
        try:
            from language import LocalEmbedder
            return LocalEmbedder()
        except Exception as exc:  # noqa: BLE001 — grounding is optional; degrade to none
            sys.stderr.write(f"[warn] LocalEmbedder unavailable ({exc}); grounding off\n")
    return None
