"""One in-flight off-loop Narsese-distillation job (v1.24.0 Sprint 3) — daemon side, Imperative Shell.

Mirrors SummaryJob/RecallJob: a `safespawn` child (`service.learn_worker`) whose stdout the daemon's
select() loop multiplexes via `extra_fds`/`handle_fd`, so the LLM claim-extraction never blocks the
reasoning loop. Store-free — it carries only the (token, source) and the worker handle; the daemon commits
the returned beliefs to L1+L2 on the main thread.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import safespawn

_TAGS = ("result", "error")
# `-m service.learn_worker` resolves the worker (and language/*) from the cwd; pin it to src/ (this file is
# src/service/learn_job.py) so the spawn works regardless of the daemon's launch directory (recall_job fix).
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LearnJob:
    def __init__(self, text: str, token: int, source: str = "") -> None:
        self.token = token
        self.source = source
        # v1.24.0 redesign: pass the SOURCE PATH as argv — the worker reads & chunks the RAW file off-loop
        # (Path B direct extraction). `text` is still piped as a fallback for a caller with no backing file.
        self._proc = safespawn.popen(
            [sys.executable, "-m", "service.learn_worker", source], cwd=_SRC,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self._proc.stdin.write((text or "").encode())
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        self._buf = b""

    def fileno(self) -> int:
        return self._proc.stdout.fileno()

    def read(self) -> list[tuple[str, object]]:
        """Drain readable bytes (won't block — only what select() flagged); parse complete lines into
        `(tag, payload)` events. An `("eof", None)` marks the worker exited (drain done)."""
        data = os.read(self._proc.stdout.fileno(), 65536)
        events: list[tuple[str, object]] = []
        if data:
            self._buf += data
            parts = self._buf.split(b"\n")
            self._buf = parts.pop()
            for raw in parts:
                ev = self._parse(raw)
                if ev is not None:
                    events.append(ev)
            return events
        if self._buf.strip():
            ev = self._parse(self._buf)
            if ev is not None:
                events.append(ev)
        self._buf = b""
        self._proc.wait()
        events.append(("eof", None))
        return events

    @staticmethod
    def _parse(raw: bytes) -> tuple[str, object] | None:
        s = raw.decode(errors="ignore").strip()
        for tag in _TAGS:
            prefix = f"[{tag}] "
            if s.startswith(prefix):
                try:
                    return (tag, json.loads(s[len(prefix):]))
                except ValueError:
                    return (tag, s[len(prefix):])
        return None

    def cleanup(self) -> None:
        try:
            self._proc.stdout.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=1)
        except Exception:  # noqa: BLE001
            pass
