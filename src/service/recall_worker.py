"""Ephemeral, isolated Stage-4 worker (ADR-056 / Gate 2) — Imperative Shell (S-02).

Runs as its OWN process: `python -m service.recall_worker`, reading the payload as JSON on stdin. It
spins a FRESH, isolated OpenNARS, loads ONLY the handed `top_k` beliefs, runs the derivation, extracts
the evidential STAMP, writes exactly one `[result]` line, and exits.

Why a separate PROCESS (not a thread): a pathological deep-transitive query can churn ONA for tens of
seconds. A thread can't be killed; a process can. The daemon enforces a hard 5 s ceiling by SIGKILL —
which is only possible because Stage 4 lives here, fully detached.

Payload boundary (ratified): the worker receives ONLY raw Narsese strings + the question. No MemoryStore,
no socket, no private state — so it cannot leak anything and the daemon owns all enrichment/IO.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        beliefs = req.get("beliefs", [])
        question = str(req.get("question", ""))
    except Exception:  # noqa: BLE001
        sys.stdout.write('[result] ' + json.dumps({"grounded": False}) + "\n"); sys.stdout.flush()
        return 0
    if not question:
        sys.stdout.write('[result] ' + json.dumps({"grounded": False}) + "\n"); sys.stdout.flush()
        return 0

    from brain import Brain
    from memory.fact import to_statement
    brain = Brain(cycles_per_step=300)
    try:
        for b in beliefs:
            brain.add_belief(to_statement(b["narsese"], float(b.get("frequency", 1.0)),
                                          float(b.get("confidence", 0.9))))
        answer = brain.ask(question)
        if answer is None:
            out: dict = {"grounded": False}
        else:
            out = {"grounded": True, "answer": answer.term, "stamp": brain.evidence_terms(answer.stamp),
                   "truth": ({"frequency": answer.truth.frequency, "confidence": answer.truth.confidence}
                             if answer.truth else None)}
    finally:
        brain.close()
    sys.stdout.write('[result] ' + json.dumps(out) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
