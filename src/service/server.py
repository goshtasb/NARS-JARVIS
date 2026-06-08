"""The headless JARVIS daemon — a single-threaded unix-domain-socket server hosting one Session.

Concurrency model (deliberate, same discipline as the old console): ONE thread multiplexes the
listening socket, every connected client, and the sensor pipe via select(); on timeout it ticks the
M2 system sentinel. So the two ONA subprocesses and the actuator are single-owner BY CONSTRUCTION —
no locks. Requests are dispatched to the Session; events the Session emits are broadcast to all
connected clients. Run: `python3 -m service` (or `service.server`).
"""
from __future__ import annotations

import os
import select
import socket

from . import protocol
from .paths import socket_path
from .session import Session


class Daemon:
    def __init__(self, db_path: str = "jarvis.db", sock_path: str | None = None,
                 poll_interval: float = 2.0) -> None:
        self._path = sock_path or socket_path()
        self._poll = poll_interval
        self._clients: dict[socket.socket, protocol.LineBuffer] = {}
        self._session = Session(db_path, on_event=self._broadcast)
        self._srv: socket.socket | None = None

    def _broadcast(self, kind: str, body: dict) -> None:
        frame = protocol.encode(protocol.event(kind, body))
        for sock in list(self._clients):
            try:
                sock.sendall(frame)
            except OSError:
                self._drop(sock)

    def serve(self) -> None:
        if os.path.exists(self._path):
            os.unlink(self._path)
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self._path)
        self._srv.listen(8)
        try:
            while True:
                # The session contributes child-process fds (sensor pipe + any in-flight whisper
                # jobs); we multiplex them alongside clients so ML never blocks the loop.
                watch: list = [self._srv, *self._clients, *self._session.extra_fds()]
                ready, _, _ = select.select(watch, [], [], self._poll)
                if not ready:
                    self._session.tick()
                    continue
                for obj in ready:
                    if obj is self._srv:
                        self._accept()
                    elif isinstance(obj, int):          # a session-owned fd (sensor / whisper stdout)
                        self._session.handle_fd(obj)
                    else:
                        self._handle(obj)
                if self._session.wants_shutdown():      # `shutdown` command -> clean exit (kill switch)
                    break
        finally:
            self._session.close()
            for sock in list(self._clients):
                self._drop(sock)
            self._srv.close()
            if os.path.exists(self._path):
                os.unlink(self._path)

    def _accept(self) -> None:
        sock, _ = self._srv.accept()
        self._clients[sock] = protocol.LineBuffer()

    def _drop(self, sock: socket.socket) -> None:
        self._clients.pop(sock, None)
        try:
            sock.close()
        except OSError:
            pass

    def _handle(self, sock: socket.socket) -> None:
        try:
            data = sock.recv(65536)
        except OSError:
            data = b""
        if not data:
            self._drop(sock)
            return
        for frame in self._clients[sock].feed(data):
            if frame.get("t") != protocol.REQ:
                continue
            ok, body = self._session.dispatch(frame.get("cmd", ""), frame.get("arg", ""))
            try:
                sock.sendall(protocol.encode(protocol.response(frame.get("id", 0), ok, body)))
            except OSError:
                self._drop(sock)


def main() -> None:
    from safespawn import scrub_environ
    scrub_environ()  # ADR-015: purge secrets from os.environ BEFORE any model/subprocess spawn
    Daemon(db_path=os.environ.get("NARS_JARVIS_DB", "jarvis.db")).serve()


if __name__ == "__main__":
    main()
