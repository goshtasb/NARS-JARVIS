"""macOS telemetry sensor — builds & launches the unprivileged Swift helper, maps app -> category.

Imperative Shell (S-02): the helper is a separate process emitting 'activate/launch/idle/ready'
lines on stdout; the console select()s on that pipe (no PyObjC, no CFRunLoop in our event loop).
The ONLY thing we derive from an app is its coarse CATEGORY — never window titles/contents — which
is exactly the line that keeps us out of macOS TCC dialogs.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "sensor.swift"
_BIN = Path(__file__).resolve().parent / ".sensor.bin"  # gitignored build artifact

# Bundle id (exact or prefix) -> coarse category. Low-cardinality by design.
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "editor": ("com.microsoft.vscode", "com.todesktop", "com.apple.dt.xcode",
               "com.sublimetext", "com.jetbrains", "dev.zed"),
    "browser": ("com.apple.safari", "com.google.chrome", "org.mozilla.firefox",
                "com.brave.browser", "company.thebrowser.browser"),
    "comms": ("com.tinyspeck.slackmacgap", "com.microsoft.teams", "com.apple.mobilesms",
              "com.hnc.discord", "us.zoom.xos", "com.apple.mail"),
    "terminal": ("com.apple.terminal", "com.googlecode.iterm2", "dev.warp.warp-stable"),
}


def category(bundle_id: str) -> str:
    """Map a bundle id to a coarse category (the only app attribute we ever read). Pure."""
    b = bundle_id.lower()
    for cat, ids in _CATEGORIES.items():
        if any(b == i or b.startswith(i) for i in ids):
            return cat
    return "other"


def build_sensor() -> Path | None:
    """Compile sensor.swift to .sensor.bin if needed. None if swiftc/source unavailable or fails."""
    if not shutil.which("swiftc") or not _SRC.exists():
        return None
    if _BIN.exists() and _BIN.stat().st_mtime >= _SRC.stat().st_mtime:
        return _BIN
    result = subprocess.run(["swiftc", "-O", str(_SRC), "-o", str(_BIN)],
                            capture_output=True, text=True, timeout=120)
    return _BIN if result.returncode == 0 else None


class Sensor:
    """Owns the helper subprocess. Yields raw event lines; the caller funnels + discretizes them."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def start(self) -> bool:
        binary = build_sensor()
        if binary is None:
            return False
        self._proc = subprocess.Popen([str(binary)], stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, bufsize=1)
        return True

    def stream(self):
        """The helper's stdout stream object — pass to select() and key dispatch on identity."""
        return self._proc.stdout if self._proc else None

    def fileno(self) -> int | None:
        return self._proc.stdout.fileno() if (self._proc and self._proc.stdout) else None

    def readline(self) -> str:
        return self._proc.stdout.readline() if (self._proc and self._proc.stdout) else ""

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
