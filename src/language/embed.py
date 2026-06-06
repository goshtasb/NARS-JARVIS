"""Local embedding client (llama.cpp) — Imperative Shell (S-02). For grounding dedup (R1).

Strictly local / air-gapped (NFR-1/2). Requires `llama-cpp-python` and a local GGUF
embedding model (e.g. nomic-embed-text). `llama_cpp` is imported lazily so the pure layers
import and test without a model present. See README for setup.
"""
from __future__ import annotations

import os
from pathlib import Path


class LocalEmbedder:
    """Wraps a local GGUF embedding model; returns dense vectors for grounding."""

    def __init__(self, model_path: str | None = None) -> None:
        path = model_path or os.environ.get("NARS_JARVIS_EMBED_GGUF")
        if not path or not Path(path).exists():
            raise FileNotFoundError(
                "Set NARS_JARVIS_EMBED_GGUF to a local GGUF embedding model "
                "(e.g. nomic-embed-text; see language/README.md)."
            )
        from llama_cpp import Llama  # lazy

        self._llm = Llama(model_path=path, embedding=True, verbose=False)

    def embed(self, text: str) -> list[float]:
        return self._llm.embed(text)
