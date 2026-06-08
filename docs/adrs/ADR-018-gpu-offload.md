# ADR-018: GPU offload for the local models (Metal)

## Status
Accepted. Performance fix — the engine was running the 7B on CPU.

## Context
The user reported the engine was "painfully slow." Root cause: `LocalLLM`/`LocalEmbedder` constructed
`Llama(...)` **without `n_gpu_layers`**, and llama-cpp-python defaults to **`n_gpu_layers=0` (CPU-only)**
— so the 7B chat model and the embedder ran on the CPU even though the host is an **Apple M3 Pro with
Metal** (the runtime confirmed the GPU initialized).

Measured A/B on the 7B Q4 (M3 Pro):
| | load | generation |
| --- | --- | --- |
| CPU (`n_gpu_layers=0`, the old default) | 17.1 s | 19.0 tok/s |
| Metal (`n_gpu_layers=-1`) | 1.2 s | 27.9 tok/s |

## Decision
Offload all layers to the GPU by default: `n_gpu_layers = int(os.environ.get("NARS_JARVIS_GPU_LAYERS",
"-1"))` passed to `Llama(...)` in both `language/llm.py` and `language/embed.py`. `-1` = all layers;
override via `NARS_JARVIS_GPU_LAYERS` (e.g. `0` to force CPU on a GPU-less host or if a model doesn't
fit VRAM).

## Consequences
- **Gained:** ~14× faster model load (matters on every daemon restart) and ~1.5× faster generation;
  end-to-end a short reply now lands in ~4 s. Configurable + reversible.
- **Honest limits:** the *generation* speedup is modest (~1.5×), not a magic order-of-magnitude — 7B on
  a laptop is inherently multi-second per reply (the M3 CPU path is already decent via Accelerate, and
  Metal tensor-accel is partly limited pre-M5). For dramatically faster, the lever is a smaller model
  (the 3B, ~2× but lower quality — ADR-007 rejected it as too weak) or trimming `converse`'s
  `max_tokens` (currently 512) — both deferred as they trade quality/completeness for speed.
- **No test surface:** the change is in the model-loading shell (not unit-exercised); `pytest` stays
  272. Verified live: daemon boots and answers on the offloaded path.

## Alternatives Considered
- **Hardcode `n_gpu_layers=-1`:** rejected — env override keeps CPU-only hosts / VRAM-tight cases working.
- **Switch to the 3B / cut max_tokens:** deferred — quality trade-offs, separate decision.
