"""Local LLM client (llama.cpp) — Imperative Shell (S-02). GBNF-constrained generation.

Strictly local / air-gapped (NFR-1/2): no network at runtime. Requires `llama-cpp-python`
and a local GGUF chat model. `llama_cpp` is imported lazily so the pure layers
(schema/compiler/ground) import and test WITHOUT a model present. See README for setup.
"""
from __future__ import annotations

import os
from pathlib import Path

from .schema import Claim, parse_claims

_GRAMMAR_PATH = Path(__file__).resolve().parent / "grammar.gbnf"


class LocalLLM:
    """Wraps a local GGUF chat model; generates schema-valid claims via the GBNF grammar."""

    def __init__(self, model_path: str | None = None, n_ctx: int = 4096) -> None:
        path = model_path or os.environ.get("NARS_JARVIS_LLM_GGUF")
        if not path or not Path(path).exists():
            raise FileNotFoundError(
                "Set NARS_JARVIS_LLM_GGUF to a local GGUF chat model "
                "(pip install llama-cpp-python; see language/README.md)."
            )
        from llama_cpp import Llama, LlamaGrammar  # lazy: keeps pure layers model-free

        # Offload all layers to the GPU (Metal on Apple Silicon). The llama-cpp default is CPU-only
        # (n_gpu_layers=0), which left the 7B running on CPU — ~14x slower to load and ~1.5x slower to
        # generate on this M3 Pro. -1 = all layers; override with NARS_JARVIS_GPU_LAYERS (e.g. 0 to
        # force CPU on a GPU-less host or if a model doesn't fit VRAM).
        n_gpu_layers = int(os.environ.get("NARS_JARVIS_GPU_LAYERS", "-1"))
        self._grammar = LlamaGrammar.from_file(str(_GRAMMAR_PATH))
        self._llm = Llama(model_path=path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, verbose=False)

    def generate(self, system_prompt: str, sentence: str) -> str:
        """Generate the raw, GBNF-constrained claim JSON for one sentence (temp 0)."""
        out = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": sentence},
            ],
            grammar=self._grammar,
            temperature=0.0,
            max_tokens=512,
        )
        return out["choices"][0]["message"]["content"]

    def to_claims(self, system_prompt: str, sentence: str) -> list[Claim]:
        """Convenience: generate + parse into typed claims."""
        return parse_claims(self.generate(system_prompt, sentence))

    def generate_text(self, system_prompt: str, user: str, max_tokens: int = 64) -> str:
        """Free-text generation (NO grammar) for the outbound voice formatter. Temp 0, bounded.
        Output is treated as untrusted prose and validated by language.voice.sanitize_voice."""
        out = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
        )
        return out["choices"][0]["message"]["content"]
