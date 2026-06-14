"""One in-flight off-loop deviation-scan job (Slice 3a) — daemon side, Imperative Shell.

Mirrors LearnJob/SummaryJob exactly: a `safespawn` child (`service.triage_worker <path> <db_path>`) whose
stdout the daemon's select() loop multiplexes via `extra_fds`/`handle_fd`, so the heavy re-parse + 3x
consensus parameter extraction never blocks the reasoning loop. Store-free — it carries only the handle; the
daemon emits the progressive-UI events on the main thread from the worker's tagged lines.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import safespawn

_TAGS = ("pending", "result", "error")
# pin `-m service.triage_worker` to src/ so the spawn resolves regardless of the daemon's launch dir.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TriageJob:
    def __init__(self, path: str, db_path: str, token: int) -> None:
        self.token = token
        self.path = path
        # path + db_path are passed as argv — the worker re-reads & re-parses the RAW file off-loop and
        # persists to the SAME WAL store the daemon owns (concurrent-safe via dbconn's busy_timeout).
        self._proc = safespawn.popen(
            [sys.executable, "-m", "service.triage_worker", path, db_path], cwd=_SRC,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._buf = b""

    def fileno(self) -> int:
        return self._proc.stdout.fileno()

    def read(self) -> list[tuple[str, object]]:
        """Drain readable bytes (non-blocking — only what select() flagged); parse complete lines into
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
