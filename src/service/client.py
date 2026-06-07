"""Thin client for the JARVIS daemon — the only code a UI needs to talk to the brain.

`call()` is a blocking request/response that *also* dispatches any event frames seen while waiting,
so async sentinel alerts are never lost. `pump()` drains events when the caller's own select loop
reports the socket readable outside a call. No reasoning lives here — just framing over the socket.
"""
from __future__ import annotations

import socket
from typing import Callable

from . import protocol
from .paths import socket_path

EventHandler = Callable[[str, dict], None]


class Client:
    def __init__(self, sock_path: str | None = None) -> None:
        self._path = sock_path or socket_path()
        self._sock: socket.socket | None = None
        self._buf = protocol.LineBuffer()
        self._rid = 0
        self._on_event: EventHandler = lambda kind, body: None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self._path)

    def set_event_handler(self, fn: EventHandler) -> None:
        self._on_event = fn

    def fileno(self) -> int:
        return self._sock.fileno()

    def call(self, cmd: str, arg: object = "") -> tuple[bool, object]:
        """Send a request and block for its correlated response, dispatching events meanwhile."""
        self._rid += 1
        rid = self._rid
        self._sock.sendall(protocol.encode(protocol.request(rid, cmd, arg)))
        while True:
            for frame in self._buf.feed(self._recv()):
                if frame.get("t") == protocol.EVT:
                    self._on_event(frame.get("kind", ""), frame.get("body") or {})
                elif frame.get("t") == protocol.RES and frame.get("id") == rid:
                    return frame.get("ok", False), frame.get("body")

    def pump(self) -> None:
        """Drain whatever is currently readable, dispatching event frames (call from a select loop)."""
        for frame in self._buf.feed(self._recv()):
            if frame.get("t") == protocol.EVT:
                self._on_event(frame.get("kind", ""), frame.get("body") or {})

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _recv(self) -> bytes:
        data = self._sock.recv(65536)
        if not data:
            raise ConnectionError("daemon closed the connection")
        return data
