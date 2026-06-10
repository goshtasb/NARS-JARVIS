# ADR-032: Read-only work primitives (read_file + summarize_file, Map-Reduce)

## Status
Accepted & live-verified. Gives the ADR-031 overnight engine a real vocabulary: read a local document
and summarize it, entirely read-only/local. Suite 417 → **430**. Tagged **v1.7.0**.

## Context
ADR-031 shipped the overnight queue but its honest limit was a near-empty vocabulary — the only
read-only actions were `find_file` + `report_system`, so unattended runs could search for files and
report CPU. The user chose "work primitives first" (before the block-based Batch Canvas) so the engine
can do something genuinely useful overnight. These are the first actions that *produce work*.

## Decision
Two new `kind="work"` catalog actions, executed read-only and writing only to a `/tmp` scratchpad —
so they pass the ADR-031 safe-autonomous boundary unchanged.

- **`read_file <path>`** — extract a local document's text (text-family via stdlib UTF-8; **`.pdf` via
  lazily-imported `pypdf`**) → scratchpad `.txt`, return a preview + path. Byte/page-capped; `.docx`/
  `.pptx` unsupported (honest message); never raises (mirrors `files.find_file`).
- **`summarize_file <path>`** — read + **recursive Map-Reduce** summarize → scratchpad `.summary.md`.

**Map-Reduce, never a naive truncator (the load-bearing rule).** `summarize` chunks the *whole*
document (conservative char-based chunks well under `n_ctx=4096`), summarizes each chunk (Map), then
recursively summarizes the summaries (Reduce) until one pass fits. A summarizer that sliced off the
first 6k tokens and discarded 34 of 40 pages while presenting itself as "the summary" would be a lie of
omission — a direct violation of the project's factual-reporting mandate. When a document exceeds the
bounded `max_chunks` cap, the output **prepends an explicit coverage note** ("summarized the first N of
M sections") rather than silently dropping content. A test asserts every chunk is mapped.

**The seam: LLM injection into the action layer.** `summarize_file` needs the 7B, but `ActionRunner`
previously had only a `spawn`. `ActionRunner.__init__(spawn, llm=None)` now carries the model; `perform`
threads it to a `kind="work"` branch that wraps `llm.generate_text` into the injected
`generate(system, user, max_tokens)` callable `documents.summarize` consumes. Tests pass a fake
`generate`, so the Map-Reduce is fully testable with **no model**. If no model is present (offline
`DemoClaims`), `summarize_file` returns an honest "no local model available" — the same duck-type guard
`Jarvis` already uses. Session injects the single existing `LocalLLM`; side benefit:
`[[DO: summarize_file: <path>]]` now also works conversationally.

**First dependency manifest.** `requirements.txt` is added documenting the now-two runtime deps:
`llama-cpp-python` (existing, implicit) and `pypdf` (new — pure-Python, local, **no network**, so the
air-gap invariant is untouched; it's a minimal-deps *preference* cost, ratified with the user).

## Consequences
- **Gained:** the overnight engine can now read and summarize a queued document end-to-end, autonomously
  and safely (read-only + scratchpad-only). The Batch Canvas now has a real verb to author.
- **Live-verified:** `read_file` on a real PDF extracted its text via pypdf; `summarize_file` on a
  multi-section doc produced a `.summary.md` covering the whole document (13 generate calls for 12
  chunks = 12 map + 1 reduce); enqueued `summarize_file` ran **autonomously** under ADR-031 and landed
  in the Morning Briefing's Done list.
- **Tests:** +13 — `read_file_text` (text/pdf-path/missing/binary/unsupported, never raises), `chunk_text`
  (splits, drops nothing), `summarize` (**maps every chunk → reduces**, **states coverage over cap**,
  empty graceful), scratchpad write, `perform` kind="work" routing (uses model not safespawn; honest
  no-model; read_file needs no model), classifier (work is autonomous). Suite **430**.
- **Honest limits:**
  - `.docx`/`.pptx` unsupported in v1; scanned/image-only PDFs yield no text — `read_file` says so
    rather than fabricate.
  - `summarize_file` blocks the single-threaded loop while it runs (many short 7B calls). Fine
    overnight; conversational use on a large doc briefly freezes the daemon — documented.
  - 7B summary quality (bounded, temp 0) is useful scaffolding, not a human analyst.
  - Char-based chunking is conservative; token-accurate chunking via the llama tokenizer is a noted
    future refinement.
- Safety boundary, consent machine, brains, and the frozen cognitive engine: untouched.

## Alternatives Considered
- **Naive truncator (slice first 6k tokens, discard rest):** rejected — silent data loss; a factual-
  reporting violation. Map-Reduce + explicit coverage is the only honest option.
- **Text-family only, no PDF:** rejected by the user — pypdf added (pure-Python, local) to cover the
  PDF-centric workflow.
- **A separate `work/` domain module:** the logic lives in `actions/documents.py` because these *are*
  catalog actions executed by `actions/run.py` (cohesive with `files.py`/`diagnostics.py`).
