"""Off-loop GUARDED distillation worker (v1.24.0 extraction redesign) — Imperative Shell (S-02).

Runs as `python -m service.learn_worker <file_path>`. Replaces the old summarize-then-distill path (proven
strictly harmful: it lost ~80% of legal obligations and laundered a 1.5%->1500-bps hallucination into a
clean claim). The new pipeline, run entirely off the daemon's select() loop (overnight, AC-gated, so
inference cost is irrelevant), is:

    raw file text  ->  chunk (direct, NO summary)            [Path B]
                   ->  per chunk: 3-pass perturbation CONSENSUS  (drop fluttering / inverted bindings)
                   ->  4-layer + negation-tripwire verification GATE  (source-grounded, fail-closed)
                   ->  compile surviving claims to Narsese
    -> emit one [result] <json narsese list>; the daemon commits via tell(source='passive').

CPU-only (GPU_LAYERS=0 set before the model import) and DB-silent — the WhisperJob contract. A path argv
is preferred (raw extraction); stdin text is a fallback so the worker still functions if piped directly.
"""
from __future__ import annotations

import json
import os
import sys


def emit(tag: str, payload) -> None:
    sys.stdout.write(f"[{tag}] {json.dumps(payload)}\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    path = argv[0].strip() if argv and argv[0].strip() else ""
    # CPU-only — set BEFORE importing the model so it never contends for the daemon's Metal context.
    os.environ["NARS_JARVIS_GPU_LAYERS"] = "0"
    try:
        from actions import documents
        from language.consensus import extract_consensus
        from language.guarded_compile import compile_claims
        from language.llm import LocalLLM
        from language.verify_gate import verify
    except Exception as exc:  # noqa: BLE001 — a missing dep is reported on the protocol, never a traceback
        emit("error", f"worker import failed: {exc}")
        return 1

    text = documents.read_file_text(path) if path else sys.stdin.read()
    if not text or text.startswith("⚠") or not text.strip():   # ⚠ extraction problem / empty
        emit("result", [])
        return 0
    try:
        llm = LocalLLM()
    except Exception as exc:  # noqa: BLE001
        emit("error", f"model unavailable: {exc}")
        return 0

    beliefs: list[str] = []
    seen: set[str] = set()
    try:
        for chunk in documents.chunk_text(text):
            consensus_claims = extract_consensus(llm, chunk)          # 3-pass perturbation consensus
            kept = []
            for claim in consensus_claims:
                result = verify(claim, chunk)                         # source-grounded gate + tripwire
                if result.admit and result.kept is not None:
                    kept.append(result.kept)
            for belief in compile_claims(kept):                      # verified claims -> Narsese
                if belief not in seen:
                    seen.add(belief)
                    beliefs.append(belief)
    except Exception as exc:  # noqa: BLE001 — a bad extraction is reported, never fatal
        emit("error", f"extraction failed: {exc}")
        return 0
    emit("result", beliefs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
