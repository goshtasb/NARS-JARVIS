# language

## Overview
The LLM channel: natural language ↔ Narsese (PRD C1). **Local-first / air-gapped** — wired
strictly to llama.cpp (NFR-1/2). The LLM never emits raw Narsese; a **GBNF grammar** forces it
to emit typed claims (`grammar.gbnf`), which the pure compiler turns into Narsese. Term identity
is grounded via **local embeddings** + a dedup threshold (PRD R1).

## Layers (Functional Core / Imperative Shell, S-02)
- **Pure (no model, unit-tested):** `schema.py` (claim types + JSON parse), `compiler.py`
  (claims → Narsese), `ground.py` (cosine similarity + dedup decision).
- **Model shells (require local GGUF):** `llm.py` (`LocalLLM`, GBNF-constrained generation),
  `embed.py` (`LocalEmbedder`, nomic-embed-text). `llama_cpp` is imported lazily, so the pure
  layers import and test without any model.

## Local setup (the model step runs on your machine)
```sh
pip install llama-cpp-python
# Download GGUF models (e.g. via huggingface-cli), then:
export NARS_JARVIS_LLM_GGUF=/path/to/chat-model.gguf
export NARS_JARVIS_EMBED_GGUF=/path/to/nomic-embed-text.gguf
```

## Usage
```python
from language import LocalLLM, claims_to_narsese
claims = LocalLLM().to_claims(system_prompt, "Tim is a duck and is not hungry.")
narsese = claims_to_narsese(claims)   # ['<tim --> duck>.', '<tim --> [hungry]>. {0.0 0.9}']
```

## Tests
From `src/`: `python3 -m language.test_compiler` and `python3 -m language.test_ground`
(both pure — no model). Live GBNF + embedding verification requires the local GGUF models.

## Related
ADR-001 (module boundaries); PRD C1, R1 (grounding), NFR-1/2 (local-first).
