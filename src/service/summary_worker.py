"""Detached CPU summary worker (ADR-052) — Imperative Shell (S-02).

Runs as its OWN process: `python -m service.summary_worker <file> <task_id>`. The heavy Map-Reduce
LLM summarization of a document happens HERE, never on the daemon's single-threaded select() loop —
so a 40-chunk PDF can no longer freeze chat, the Sentinel, or the Passive Observation Mirror.

Three deterministic guarantees (the ratified parameters):
- **CPU-only.** Forces `NARS_JARVIS_GPU_LAYERS=0` BEFORE importing the model, so it never contends
  for the daemon's Metal/GPU context. The GGUF weights are mmap-shared with the daemon via the OS
  page cache, so loading its own `LocalLLM` is cheap (no second copy of the weights in RAM).
- **DB-silent.** Writes NOTHING to jarvis.db. It streams to stdout; the daemon owns the single queue
  write on EOF (the WhisperJob contract). So a cross-process SQLITE_BUSY collision cannot arise here.
- **Line protocol on stdout.** `[progress] <json:{i,n}>` before each map step, then exactly one
  terminal `[result] <json:str>` (the summary) or `[error] <json:str>`. One record per line, flushed.
"""
from __future__ import annotations

import json
import os
import sys


def emit(tag: str, payload) -> None:
    """Write one line-protocol record and flush so the daemon's select() sees it immediately."""
    sys.stdout.write(f"[{tag}] {json.dumps(payload)}\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if len(argv) < 1 or not argv[0].strip():
        emit("error", "usage: summary_worker <file> [task_id]")
        return 2
    path = argv[0]

    # CPU-only — set BEFORE importing the model so LocalLLM (which reads the env at construct time)
    # never grabs the GPU the foreground daemon needs for interactive latency.
    os.environ["NARS_JARVIS_GPU_LAYERS"] = "0"

    try:
        from actions import documents
        from language.llm import LocalLLM
    except Exception as exc:  # noqa: BLE001 — a missing dep is reported on the protocol, never a traceback
        emit("error", f"worker import failed: {exc}")
        return 1

    text = documents.read_file_text(path)
    if text.startswith("⚠"):                       # extraction problem -> terminal error, no model load
        emit("error", text)
        return 0

    try:
        llm = LocalLLM()                            # its own instance; weights mmap-shared with the daemon
    except Exception as exc:  # noqa: BLE001
        emit("error", f"model unavailable: {exc}")
        return 0

    def generate(system: str, user: str, max_tokens: int) -> str:
        return llm.generate_text(system, user, max_tokens=max_tokens)

    def on_step(i: int, n: int) -> None:
        emit("progress", {"i": i, "n": n})

    try:
        summary = documents.summarize(text, generate, on_step=on_step)
    except Exception as exc:  # noqa: BLE001 — never crash; report on the protocol
        emit("error", f"summarization failed: {exc}")
        return 0

    label = os.path.basename(path)
    emit("result", f"Summarized {label}:\n\n{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
