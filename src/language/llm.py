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

        self._grammar = LlamaGrammar.from_file(str(_GRAMMAR_PATH))
        self._llm = Llama(model_path=path, n_ctx=n_ctx, verbose=False)

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
