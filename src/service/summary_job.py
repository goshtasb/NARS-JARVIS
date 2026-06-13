"""One in-flight document summary, daemon side (ADR-052) — Imperative Shell (S-02).

Mirrors `voice.WhisperJob`: a `safespawn.popen` child (`service.summary_worker`) whose stdout the
daemon's select() loop watches, so a minutes-long Map-Reduce summary never blocks the reasoning loop.
`read()` drains whatever select() flagged readable (so it never blocks), splits the worker's line
protocol on newlines, and returns the parsed events — `[progress] {i,n}` deltas, then a terminal
`[result] <str>` or `[error] <str>`. The daemon, NOT this job, performs the single jarvis.db write.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import safespawn

_TAGS = ("progress", "result", "error")
# `-m service.summary_worker` resolves the worker (and `actions`/`language`) from the cwd. Pin it to src/
# (this file is src/service/summary_job.py) so the spawn works regardless of the daemon's launch cwd. The
# file_path argument is absolute (UI / overnight pass absolute paths), so setting cwd doesn't affect it.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SummaryJob:
    """A detached CPU summary worker for one queue task. `task_id`/`action`/`arg` are carried so the
    daemon can finalize the right row when the worker reports back."""

    def __init__(self, file_path: str, task_id: int, action: str = "summarize_file") -> None:
        self.task_id = task_id
        self.action = action
        self.arg = file_path
        self._proc = safespawn.popen(
            [sys.executable, "-m", "service.summary_worker", file_path, str(task_id)], cwd=_SRC,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._buf = b""

    def fileno(self) -> int:
        return self._proc.stdout.fileno()

    def read(self) -> list[tuple[str, object]]:
        """Drain readable bytes (won't block — only what select() flagged), parse complete lines.
        Returns events `(tag, payload)`; an `("eof", None)` event marks the worker exited (drain done)."""
        data = os.read(self._proc.stdout.fileno(), 65536)
        events: list[tuple[str, object]] = []
        if data:
            self._buf += data
            parts = self._buf.split(b"\n")
            self._buf = parts.pop()                       # keep the trailing partial line
            for raw in parts:
                ev = self._parse(raw)
                if ev is not None:
                    events.append(ev)
            return events
        # EOF: flush any buffered final line, wait the child, signal completion.
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
                body = s[len(prefix):]
                try:
                    return (tag, json.loads(body))
                except ValueError:
                    return (tag, body)
        return None                                       # noise / partial -> ignore

    def cleanup(self) -> None:
        try:
            self._proc.stdout.close()
        except OSError:
            pass
