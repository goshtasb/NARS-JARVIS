"""service — the headless JARVIS daemon and its IPC contract (Phase 1).

The reasoning core runs as a single-threaded daemon (`Daemon`/`Session`) behind a line-delimited
JSON protocol over a unix-domain socket; every UI (the terminal console today, SwiftUI next) is a
thin `Client`. This decoupling keeps reasoning logic out of the UI layer and lets the whole brain be
tested headlessly, with no GUI event loop. Public interface (ADR-001 / S-01); see ADR-003.
"""
from . import protocol
from .client import Client
from .paths import socket_path
from .server import Daemon
from .session import Session

__all__ = ["protocol", "Client", "Daemon", "Session", "socket_path"]
