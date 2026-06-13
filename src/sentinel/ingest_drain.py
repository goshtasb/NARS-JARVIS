"""Passive Context Ingestion — the overnight drain (v1.24.0, Sprint 2).

Pulls validated candidates from the IngestQueue and hands the changed ones to the chunker. The 2 PM
capture event is treated strictly as a STALE HINT: every file is re-validated at drain time. The whole
per-row sequence is wrapped so one dead path can never poison the batch (the blast-radius discipline from
PR #2), and retries are temporally backed off + capped so a yanked external drive isn't chased forever.

Validation sequence (each failure -> a state transition, never a thrown error):
  1. containment   — realpath must still be inside the watch root (scope may have changed) -> gone
  2. stat          — FileNotFoundError -> gone (terminal); other OSError (unmounted drive) -> backoff retry
  3. regular file  — became a dir / FIFO / symlink-loop -> gone
  4. size cap      — grew past the hard cap -> gone
  5. fast path     — mtime unchanged since last drain -> skip (done, no hash, no inference)
  6. content hash  — equals the last-drained hash -> skip (done, no inference); else ->
  7. ingest        — hand to ingest_fn (the SummaryJob chunker); ok -> done(+hash,+mtime); error -> backoff
"""
from __future__ import annotations

import hashlib
import os
import stat as statmod
import sys
import time
from typing import Callable

_HARD_CAP = 10 * 1024 * 1024   # never ingest a file bigger than this (matches the capture edge)
_CAP = 3                       # max transient/ingest retries before a terminal state
_BACKOFF_S = 3600.0            # temporal backoff: defer a transient retry ~1h (time to reconnect hardware)


class IngestDrain:
    def __init__(self, queue, watch_root: str, ingest_fn: Callable[[str], None], *,
                 cap: int = _CAP, backoff_s: float = _BACKOFF_S, hard_cap: int = _HARD_CAP,
                 clock: Callable[[], float] = time.time, stat_fn: Callable[[str], os.stat_result] = os.stat) -> None:
        self._q = queue
        self._watch_real = os.path.realpath(watch_root)
        self._ingest = ingest_fn                 # the splice: a validated, changed file -> the chunker
        self._cap = cap
        self._backoff = backoff_s
        self._hard_cap = hard_cap
        self._clock = clock
        self._stat = stat_fn

    def drain_once(self, *, on_ac: bool = True) -> str | None:
        """Claim and process at most one candidate. Returns the path acted on (or None if the queue is
        empty / nothing eligible). Wrapped so no exception can escape into the daemon loop."""
        now = self._clock()
        row = self._q.claim_next(now, on_ac)
        if row is None:
            return None
        try:
            self._process(row, now)
        except Exception as exc:  # noqa: BLE001 — final safety net; a dead path never poisons the batch
            sys.stderr.write(f"[ingest_drain] unexpected error on {row['path']}: {exc}\n")
            self._retry(row, now, terminal="failed")
        return row["path"]

    def _process(self, row: dict, now: float) -> None:
        rid, path = row["id"], row["path"]
        # 1. containment — the watch scope may have been renamed/narrowed since capture
        if not self._within(path):
            self._q.mark_terminal(rid, "gone")
            return
        # 2. stat — distinguish a hard delete (terminal) from a transient OS error (backoff)
        try:
            st = self._stat(path)
        except FileNotFoundError:
            self._q.mark_terminal(rid, "gone")
            return
        except OSError:                                   # ENXIO/EIO/perm — e.g. an unmounted drive
            self._retry(row, now, terminal="gone")
            return
        # 3. regular file only
        if not statmod.S_ISREG(st.st_mode):
            self._q.mark_terminal(rid, "gone")
            return
        # 4. size cap (it may have grown since capture)
        if st.st_size > self._hard_cap:
            self._q.mark_terminal(rid, "gone")
            return
        # 5. fast path: mtime unchanged since the last successful drain -> nothing to do
        if row["content_hash"] and row["mtime"] == st.st_mtime:
            self._q.mark_done(rid, row["content_hash"], st.st_mtime)
            return
        # 6. content hash: a metadata-only change (touch / re-save of identical bytes) -> skip inference
        digest = self._hash(path)
        if digest is None:                                # vanished mid-read or unreadable -> transient
            self._retry(row, now, terminal="gone")
            return
        if digest == row["content_hash"]:
            self._q.mark_done(rid, digest, st.st_mtime)   # identical content already ingested
            return
        # 7. genuinely new/changed content -> ingest (the only path that spends LLM inference)
        try:
            self._ingest(path)
        except Exception as exc:  # noqa: BLE001 — the chunker handoff failed -> bounded retry, then 'failed'
            sys.stderr.write(f"[ingest_drain] ingest handoff failed for {path}: {exc}\n")
            self._retry(row, now, terminal="failed")
            return
        self._q.mark_done(rid, digest, st.st_mtime)

    def _retry(self, row: dict, now: float, *, terminal: str) -> None:
        """Bounded temporal backoff: defer ~backoff seconds and bump attempts; at the cap, go terminal."""
        if row["attempts"] + 1 >= self._cap:
            self._q.mark_terminal(row["id"], terminal)
        else:
            self._q.schedule_retry(row["id"], now + self._backoff)

    def _within(self, path: str) -> bool:
        try:
            resolved = os.path.realpath(path)
        except OSError:
            return False
        try:
            return os.path.commonpath([self._watch_real, resolved]) == self._watch_real
        except ValueError:
            return False

    def _hash(self, path: str) -> str | None:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None
