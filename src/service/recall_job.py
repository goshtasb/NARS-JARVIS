"""One in-flight off-loop Stage-4 derivation, daemon side (ADR-056 / Gate 2) — Imperative Shell (S-02).

Mirrors `SummaryJob`/`WhisperJob`: a `safespawn.popen` child (`service.recall_worker`) whose stdout the
daemon's select() loop watches, so a pathological ONA derivation never blocks the reasoning loop. Adds
the **time-bomb**: a hard deadline; if the worker doesn't answer in time the daemon `kill()`s it
(SIGKILL) and reaps it (no zombies), then escalates to Cloud.

The job is store-free — it carries only the (token, deadline) and the worker handle. Enrichment of the
returned STAMP happens on the main thread (which owns the store).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import safespawn

TIMEOUT_S = 5.0     # the hard ceiling — past this the worker is SIGKILL'd and the query escalates to Cloud


class RecallJob:
    def __init__(self, beliefs: list[dict], question: str, token: int, *, timeout: float | None = None) -> None:
        self.token = token
        self.topic_hash = ""                              # set by the session; carried to the completion metric
        self.deadline = time.monotonic() + (TIMEOUT_S if timeout is None else timeout)   # read at call time
        self._proc = safespawn.popen(
            [sys.executable, "-m", "service.recall_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self._proc.stdin.write(json.dumps({"beliefs": beliefs, "question": question}).encode())
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        self._buf = b""
        self._done = False

    def fileno(self) -> int:
        return self._proc.stdout.fileno()

    def read(self) -> dict | None:
        """Drain readable bytes; on EOF (worker exited) parse the single `[result]` line and reap. Returns
        the result dict when complete, else None (still accumulating). A malformed/empty exit -> abstain."""
        data = os.read(self._proc.stdout.fileno(), 65536)
        if data:
            self._buf += data
            return None
        self._proc.wait()                                    # reap (no zombie)
        self._done = True
        s = self._buf.decode(errors="ignore").strip()
        if s.startswith("[result] "):
            try:
                return json.loads(s[len("[result] "):])
            except ValueError:
                return {"grounded": False}
        return {"grounded": False}                           # worker crashed / wrote nothing -> abstain

    def expired(self, now: float) -> bool:
        return not self._done and now >= self.deadline

    def kill(self) -> None:
        """Time-bomb: SIGKILL the worker and reap it. Idempotent."""
        if self._done:
            return
        try:
            self._proc.kill()                                # SIGKILL
        except OSError:
            pass
        try:
            self._proc.wait(timeout=2)                       # reap -> no zombie
        except Exception:  # noqa: BLE001
            pass
        self._done = True

    def cleanup(self) -> None:
        try:
            self._proc.stdout.close()
        except OSError:
            pass
        if not self._done:
            self.kill()
