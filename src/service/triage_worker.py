"""Off-loop deviation-scan worker (Slice 3a) — Imperative Shell (S-02).

Runs as `python -m service.triage_worker <file_path> <db_path>`, entirely off the daemon's select() loop
(spawned only when the AC/consent gate passes). Pipeline (see triage.devscan):

    re-parse the file -> salient spans -> 3x consensus parameter extraction (guarded) -> ParamStore
                      -> per-kind baseline (this doc excluded) -> deviations -> the UI event body.

Emits `[pending] {"salient_count": n}` once the salient set is known (so the daemon paints the Pending
state with no layout shift), then one `[result] <event-body json>`. CPU-only (GPU_LAYERS=0 set before the
model import) so it never contends for the daemon's Metal context — the WhisperJob/learn_worker contract.
A missing dep, an unreadable file, or no model is reported on the protocol as `[error]`, never a traceback.
"""
from __future__ import annotations

import json
import os
import sys


def emit(tag: str, payload) -> None:
    sys.stdout.write(f"[{tag}] {json.dumps(payload)}\n")
    sys.stdout.flush()


def triage_model_path(env) -> str | None:
    """Slice 4 hardening: triage extraction is GBNF-constrained, so the lighter 3B model extracts a
    parameter just as deterministically as the 7B at ~half the resident RAM. Prefer NARS_JARVIS_TRIAGE_GGUF
    (the 3B); fall back to the daemon's NARS_JARVIS_LLM_GGUF; None lets LocalLLM raise its own clear error."""
    return env.get("NARS_JARVIS_TRIAGE_GGUF") or env.get("NARS_JARVIS_LLM_GGUF")


def main(argv: list[str]) -> int:
    path = argv[0].strip() if argv and argv[0].strip() else ""
    db_path = argv[1].strip() if len(argv) > 1 and argv[1].strip() else ":memory:"
    if not path or not os.path.isfile(path):
        emit("error", f"no such file: {path}")
        return 0
    # CPU-only — set BEFORE importing the model so it never contends for the daemon's Metal context.
    os.environ["NARS_JARVIS_GPU_LAYERS"] = "0"
    try:
        from language.llm import LocalLLM
        from triage.devscan import scan_document
        from triage.paramstore import ParamStore
    except Exception as exc:  # noqa: BLE001 — a missing dep is reported on the protocol, never a traceback
        emit("error", f"worker import failed: {exc}")
        return 1

    try:
        llm = LocalLLM(model_path=triage_model_path(os.environ))   # 3B (lighter) for the constrained extract
    except Exception as exc:  # noqa: BLE001
        emit("error", f"model unavailable: {exc}")
        return 0

    store = ParamStore(db_path)
    try:
        body = scan_document(path, llm=llm, store=store,
                             on_pending=lambda n: emit("pending", {"salient_count": n}))
        emit("result", body)
    except Exception as exc:  # noqa: BLE001 — a bad scan is reported, never fatal
        emit("error", f"scan failed: {exc}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
