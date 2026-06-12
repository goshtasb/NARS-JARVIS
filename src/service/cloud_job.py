"""Off-loop cloud execution (ADR-056, Vector 3) — Imperative Shell (S-02).

A cloud completion is several seconds of network I/O. Run on the daemon's single select() loop it would
freeze chat, sensing, and the Mirror exactly like a blocking local inference (ADR-003). `CloudJob` runs
the call in a **background thread** and signals completion through a **self-pipe** the daemon `select()`s
— the same fd-multiplex shape as the WhisperJob / summary worker (ADR-052).

Why a thread, not a subprocess: the blocking work is `urllib` socket I/O, which **releases the GIL**, so
the main thread keeps running select() and draining the Sentinel's sensor fds while the call is in
flight. The API key never leaves the process (no serialization to a child), honoring the
credential-stateless ruling. The job touches no shared daemon state — it only computes a `CloudResult`
and writes one wake byte — so single-threaded invariants hold.

Lifecycle: `job = CloudJob(thunk)` starts it; register `job.fileno()` in the session's extra fds;
when select() reports it readable, call `job.result()` (drains the wake byte, returns the CloudResult),
then `job.close()`.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from cloud_egress import CloudResult


class CloudJob:
    def __init__(self, run: Callable[[], CloudResult]):
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)
        self._result: Optional[CloudResult] = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._work, args=(run,), daemon=True)
        self._thread.start()

    def _work(self, run: Callable[[], CloudResult]) -> None:
        try:
            self._result = run()
        except Exception as exc:  # noqa: BLE001 — a thrown driver never takes down the daemon
            from cloud_egress import CloudResult as _R
            self._result = _R(ok=False, kind="network", error=f"Cloud call failed: {exc}")
        finally:
            self._done.set()
            try:
                os.write(self._w, b"x")        # wake the select loop (idempotent: one byte)
            except OSError:
                pass

    def fileno(self) -> int:
        """The readable end the daemon select()s on; becomes readable exactly when the result is ready."""
        return self._r

    def ready(self) -> bool:
        return self._done.is_set()

    def result(self) -> Optional[CloudResult]:
        """Drain the wake byte and return the CloudResult (None if not finished yet)."""
        try:
            os.read(self._r, 64)
        except (BlockingIOError, OSError):
            pass
        return self._result

    def close(self) -> None:
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass
