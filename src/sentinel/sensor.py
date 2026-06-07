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

# Our closed, coarse taxonomy — aligned to Apple's own UTI app categories so novel apps self-classify.
BUCKETS = ("dev", "web", "comms", "media", "productivity", "utility", "other")

# Apple's fixed LSApplicationCategoryType UTIs -> our buckets. The taxonomy is Apple's, not ours,
# so a never-before-seen app inherits a sensible bucket from its own Info.plist for free.
_UTI_BUCKET: dict[str, str] = {
    "public.app-category.developer-tools": "dev",
    "public.app-category.social-networking": "comms",
    "public.app-category.productivity": "productivity",
    "public.app-category.business": "productivity",
    "public.app-category.utilities": "utility",
    "public.app-category.education": "productivity",
    "public.app-category.music": "media",
    "public.app-category.video": "media",
    "public.app-category.photography": "media",
    "public.app-category.entertainment": "media",
    "public.app-category.graphics-design": "media",
    "public.app-category.news": "web",
}

# Override ONLY where Apple's metadata is missing or wrong. Browsers have no "web" UTI; terminals
# and some comms apps mis-declare. A small, stable list — not the primary mechanism.
_OVERRIDE: dict[str, str] = {  # bundle-id prefix -> bucket
    "com.apple.safari": "web", "com.google.chrome": "web", "org.mozilla.firefox": "web",
    "com.brave.browser": "web", "company.thebrowser": "web",  # Arc
    "com.apple.terminal": "dev", "com.googlecode.iterm2": "dev", "dev.warp": "dev",
    "com.tinyspeck.slackmacgap": "comms", "com.microsoft.teams": "comms",
    "com.hnc.discord": "comms", "us.zoom.xos": "comms", "com.apple.mail": "comms",
}


def bucket_for_uti(ls_category: str) -> str:
    """Map an Apple LSApplicationCategoryType UTI to our bucket; 'other' if unknown/missing. Pure."""
    return _UTI_BUCKET.get(ls_category.strip().lower(), "other")


def classify(bundle_id: str, ls_category: str = "") -> str:
    """Resolve a bundle to a bucket: explicit override first, else the app's self-declared UTI,
    else 'other'. Pure; the SQLite memoizer caches this so the work happens once per novel app."""
    b = bundle_id.strip().lower()
    for prefix, bucket in _OVERRIDE.items():
        if b == prefix or b.startswith(prefix):
            return bucket
    return bucket_for_uti(ls_category)


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
