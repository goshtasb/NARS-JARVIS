"""Passive Context Ingestion — the Python receptor for the FSEvents edge (v1.24.0, Sprint 1).

Mirrors `sentinel.sensor.Sensor`: owns the `.fswatch.bin` helper subprocess, exposes `fileno()` for the
daemon's select() loop, and drains its stdout. On each flushed JSON line it RE-VALIDATES the denylist
(defense-in-depth — the edge already dropped noise, but we never trust the wire), enforces the size cap +
micro-ingest budget, and enqueues survivors to the durable `IngestQueue`. The expensive ingest itself is
the overnight runner's job — this layer only ever stats files and writes small SQLite rows.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import safespawn

from .ingest_queue import IngestQueue

_SRC = Path(__file__).resolve().parent / "fswatch.swift"
_BIN = Path(__file__).resolve().parent / ".fswatch.bin"   # gitignored build artifact (like .sensor.bin)

# Re-validation (must mirror fswatch.swift — defense-in-depth, the daemon never trusts the edge).
_DENY_DIRS = {"node_modules", ".git", ".svn", ".hg", "build", "dist", ".next", "target", "__pycache__",
              ".cache", "Caches", "DerivedData", ".venv", "venv", "vendor", ".Trash", "Pods", ".gradle",
              "bin", "obj"}
_DENY = tuple(f"/{d}/" for d in _DENY_DIRS)
_KEEP_EXT = {"txt", "md", "markdown", "rst", "pdf", "py", "js", "ts", "tsx", "jsx", "swift", "c", "h",
             "cpp", "hpp", "cc", "go", "rs", "java", "kt", "rb", "json", "yaml", "yml", "toml", "html",
             "css", "sh", "sql"}
_HARD_CAP = 10 * 1024 * 1024   # never queue a file bigger than this (absurd payloads dropped outright)
_LIGHT = 5 * 1024              # micro-ingest budget: <=5KB is "light" — eligible even on battery
_RESCAN_CAP = 500              # a coarse-rescan marker enqueues at most this many survivors (bounded)


def build_fswatch() -> Path | None:
    """Compile fswatch.swift -> .fswatch.bin if stale. None if swiftc/source unavailable or build fails."""
    if not shutil.which("swiftc") or not _SRC.exists():
        return None
    if _BIN.exists() and _BIN.stat().st_mtime >= _SRC.stat().st_mtime:
        return _BIN
    result = safespawn.run(["swiftc", "-O", str(_SRC), "-o", str(_BIN)],
                           capture_output=True, text=True, timeout=120)
    return _BIN if result.returncode == 0 else None


def _accept(path: str) -> bool:
    """Re-apply the edge filter on the daemon side: drop denied dirs + non-allowlisted extensions."""
    if any(d in path for d in _DENY):
        return False
    return os.path.splitext(path)[1].lstrip(".").lower() in _KEEP_EXT


class IngestWatcher:
    def __init__(self, db_path: str = ":memory:", watch_dir: str | None = None) -> None:
        self._queue = IngestQueue(db_path)
        self._watch = os.path.expanduser(watch_dir or "~/Desktop/VaultTest")
        self._proc: subprocess.Popen | None = None
        self._buf = ""

    @property
    def queue(self) -> IngestQueue:
        return self._queue

    def start(self) -> bool:
        binary = build_fswatch()
        if binary is None:
            return False
        os.makedirs(self._watch, exist_ok=True)   # ensure the watched dir exists (Sprint 1: ~/Desktop/VaultTest)
        self._proc = safespawn.popen([str(binary), self._watch],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return True

    def fileno(self) -> int | None:
        return self._proc.stdout.fileno() if (self._proc and self._proc.stdout) else None

    def read(self) -> int:
        """select() flagged our pipe readable: drain available bytes, parse complete JSON lines, enqueue
        survivors. Returns the number of candidates enqueued this pass. Non-blocking; never raises."""
        try:
            data = os.read(self._proc.stdout.fileno(), 65536)   # type: ignore[union-attr]
        except (OSError, AttributeError):
            return 0
        if not data:
            return 0                                            # EOF — the watcher exited
        self._buf += data.decode("utf-8", "ignore")
        enqueued = 0
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            enqueued += self.ingest_payload(obj)
        return enqueued

    def ingest_payload(self, obj: dict) -> int:
        """Process one flushed payload: a `{paths:[...]}` batch, or a `{rescan:dir}` coarse marker."""
        if "rescan" in obj:
            return self._rescan(str(obj["rescan"]))
        count = 0
        for p in (obj.get("paths") or [])[:MAX_BATCH]:
            if self._enqueue(str(p)):
                count += 1
        return count

    def _rescan(self, root: str) -> int:
        """A flood collapsed to a coarse marker: walk the dir ONCE, pruning denied dirs, capped at
        _RESCAN_CAP survivors — so even the degraded path stays bounded."""
        root = os.path.expanduser(root)
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _DENY_DIRS]   # prune in place -> never descend
            for fn in filenames:
                if self._enqueue(os.path.join(dirpath, fn)):
                    count += 1
                    if count >= _RESCAN_CAP:
                        return count
        return count

    def _enqueue(self, path: str) -> bool:
        if not _accept(path):
            return False
        try:
            size = os.path.getsize(path)
        except OSError:
            return False                                        # vanished/unreadable -> skip
        if size > _HARD_CAP:
            return False                                        # absurd payload -> never queue
        self._queue.enqueue(path, size, status=self._budget(size))
        return True

    def _budget(self, size: int) -> str:
        """Micro-ingest budget: light payloads are always eligible; a heavy one is deferred (held for AC)
        only when we're on battery below 50%."""
        if size <= _LIGHT:
            return "pending"
        batt = self._battery()
        if batt is None or batt.power_plugged or (batt.percent or 0) > 50:
            return "pending"
        return "deferred"

    @staticmethod
    def _battery():
        try:
            import psutil
            return psutil.sensors_battery()
        except Exception:  # noqa: BLE001 — no battery sensor (desktop / unsupported) -> treat as plugged
            return None

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._queue.close()


MAX_BATCH = 1000   # defensive cap on a single {paths:[...]} payload (mirrors the edge MAX_SET)
