"""Slice 4: onboarding bulk ingest — the Functional-Core helpers (folder scan, progress body, task pick).

Cold-start mitigation for the deviation engine: a user connects a folder of historical contracts, we queue
each valid, not-already-ingested PDF for the off-loop triage worker, and the per-kind baseline compounds as
the durable OvernightQueue drains. These three helpers are pure/testable; the Session owns the queue, the AC
gate, and the off-loop TriageJob spawn. Bulk ingest is TRIAGE-ONLY (params -> ParamStore) — never the
summarize/learn pipeline (that would waste AC power and pollute the chat summary history).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

ACTION = "triage_file"             # the OvernightQueue action kind for a bulk deviation scan
_HARD_CAP = 10 * 1024 * 1024       # 10 MB — matches the FSEvents ingest edge; absurd payloads dropped
_MAX_FILES = 200                   # one import is bounded (truncation is reported, never silent)


@dataclass
class FolderScan:
    to_enqueue: list[str] = field(default_factory=list)
    skipped_dup: int = 0           # content hash already in the corpus
    skipped_invalid: int = 0       # a .pdf that failed validation (oversize / symlink-escape / unreadable)
    truncated: int = 0             # survivors beyond the per-import cap


def _within(folder_real: str, path: str) -> bool:
    """The resolved file must still live under the chosen folder — a symlink may not escape it."""
    try:
        return os.path.commonpath([folder_real, os.path.realpath(path)]) == folder_real
    except (ValueError, OSError):
        return False


def scan_folder(folder: str, known_ids: set[str], doc_id_of: Callable[[str], str], *,
                max_files: int = _MAX_FILES, hard_cap: int = _HARD_CAP) -> FolderScan:
    """Enumerate *.pdf directly under `folder` (no recursion), dropping hidden / symlink-escaping / oversize
    files and any whose content hash (`doc_id_of(path)`) is already ingested. Deterministic order."""
    out = FolderScan()
    folder_real = os.path.realpath(folder)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        if name.startswith(".") or not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or not _within(folder_real, path):
            out.skipped_invalid += 1
            continue
        try:
            if os.path.getsize(path) > hard_cap:
                out.skipped_invalid += 1
                continue
        except OSError:
            out.skipped_invalid += 1
            continue
        if doc_id_of(path) in known_ids:
            out.skipped_dup += 1
            continue
        if len(out.to_enqueue) >= max_files:
            out.truncated += 1
            continue
        out.to_enqueue.append(path)
    return out


def next_triage_task(queue_rows: list[dict]) -> dict | None:
    """The next pending bulk scan (FIFO by id), or None. Drives the serial, one-at-a-time drain."""
    pend = [r for r in queue_rows if r.get("action") == ACTION and r.get("status") == "pending"]
    return min(pend, key=lambda r: r.get("id", 0)) if pend else None


def progress_body(queue_rows: list[dict], corpus_size: int) -> dict:
    """Cumulative corpus-ingest progress from the durable queue's triage_file rows: counters + a server-
    authored label (the Swift client renders the label verbatim — no string logic on the glass)."""
    rows = [r for r in queue_rows if r.get("action") == ACTION]
    total = len(rows)
    done = sum(1 for r in rows if r.get("status") == "done")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    in_flight = total - done - failed
    if total == 0:
        label = ""
    elif in_flight > 0:
        label = f"Corpus baseline: {done} of {total} documents ingested. Deviation confidence improving."
    else:
        label = f"Baseline complete: {corpus_size} contract{'' if corpus_size == 1 else 's'} in corpus."
    return {"state": "ingesting" if in_flight > 0 else "idle", "done": done, "total": total,
            "failed": failed, "in_flight": in_flight, "corpus_size": corpus_size, "label": label}
