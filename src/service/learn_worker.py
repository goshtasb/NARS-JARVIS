"""Off-loop Narsese distillation worker (v1.24.0 Sprint 3) — Imperative Shell (S-02).

Runs as `python -m service.learn_worker`, reading TEXT (a completed summary) on stdin. It loads its OWN
CPU-only `LocalLLM` (weights mmap-shared with the daemon via the OS page cache — no second copy), extracts
factual claims with the grammar-constrained model, converts them to Narsese, and writes ONE
`[result] <json-list-of-narsese>` line on stdout. The daemon commits those statements to L1 ONA + L2 store
on the main thread (ONA is single-owner). So the expensive extraction stays OFF the select() loop — the
same discipline as SummaryJob — and the worker holds no vault handle, so it can leak nothing.
"""
from __future__ import annotations

import json
import os
import sys

# Mirrors the cloud flywheel's extraction intent, minus the alias clause (the local claims grammar emits
# RelationClaim / PropertyClaim only; tell() repopulates the lexicon on commit anyway).
_EXTRACT_PROMPT = ("Extract the factual claims stated in the text as structured JSON: "
                   "subject-relation-object (RelationClaim) and subject-property (PropertyClaim). "
                   "Assert ONLY what the text states. If nothing factual is asserted, return empty lists.")
_MAX_CHARS = 8000   # the input is a (short) summary; bound it well within the model's context window


def emit(tag: str, payload) -> None:
    sys.stdout.write(f"[{tag}] {json.dumps(payload)}\n")
    sys.stdout.flush()


def main() -> int:
    text = sys.stdin.read()
    if not text.strip():
        emit("result", [])
        return 0
    # CPU-only — set BEFORE importing the model so it never contends for the daemon's Metal/GPU context.
    os.environ["NARS_JARVIS_GPU_LAYERS"] = "0"
    try:
        from language import LocalLLM, claims_to_narsese
    except Exception as exc:  # noqa: BLE001 — a missing dep is reported on the protocol, never a traceback
        emit("error", f"worker import failed: {exc}")
        return 1
    try:
        llm = LocalLLM()
    except Exception as exc:  # noqa: BLE001
        emit("error", f"model unavailable: {exc}")
        return 0
    try:
        claims = llm.to_claims(_EXTRACT_PROMPT, text[:_MAX_CHARS])
        narsese = claims_to_narsese(claims)
    except Exception as exc:  # noqa: BLE001 — a bad extraction is reported, never fatal
        emit("error", f"extraction failed: {exc}")
        return 0
    emit("result", narsese)
    return 0


if __name__ == "__main__":
    sys.exit(main())
