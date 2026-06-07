"""Where the daemon's unix-domain socket lives. Local-only by construction (a filesystem socket,
never a TCP port), honoring the offline mandate. Override with NARS_JARVIS_SOCK for tests."""
from __future__ import annotations

import os
import tempfile


def socket_path() -> str:
    return os.environ.get("NARS_JARVIS_SOCK") or os.path.join(tempfile.gettempdir(), "nars-jarvis.sock")
