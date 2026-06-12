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


def make_claim_source():
    """Returns the Multiplexer-wrapped brain (ADR-056). Default mode is **private** → it delegates
    verbatim to the local LLM, so today's behavior is unchanged until the session sets General mode
    (with a per-request key) on it. The Multiplexer is the single injection point for both brains."""
    if os.environ.get("NARS_JARVIS_LLM_GGUF"):
        try:
            from language import LocalLLM
            from language.multiplexer import Multiplexer
            return Multiplexer(LocalLLM())
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
