"""Headless proof of the IPC seam: a real daemon over a real unix socket, driven by the thin client
with NO GUI and NO terminal. Request/response plane + event-broadcast plane."""
import os
import socket
import tempfile
import threading
import time

from service import Client, Daemon, protocol


def _spawn(sock: str, db: str) -> None:
    # Build the Daemon INSIDE the thread: the real daemon is its own single-threaded process, and
    # sqlite connections are thread-bound, so the Session must be created where it is served.
    threading.Thread(target=lambda: Daemon(db_path=db, sock_path=sock, poll_interval=0.2).serve(),
                     daemon=True).start()


def _connect(sock: str) -> Client:
    c = Client(sock)
    for _ in range(200):                      # daemon boots ONA before bind(); retry until the socket is up
        try:
            c.connect(); return c
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.05)
    raise RuntimeError("daemon never came up")


def test_seam_request_response_end_to_end() -> None:
    sock = tempfile.mktemp(suffix=".sock")
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        _spawn(sock, db)
        c = _connect(sock)
        ok, body = c.call("tell", "<tim --> duck>.")           # commit a belief over the wire
        assert ok and "committed" in body["text"], body
        c.call("tell", "<duck --> bird>.")
        ok, body = c.call("ask", "<tim --> duck>?")            # query it back over the wire
        assert ok and "answer:" in body["text"], body
        ok, body = c.call("status")
        assert ok and "L2 facts" in body["text"], body
        ok, body = c.call("health")
        assert ok and "focus sentinel" in body["text"], body
        ok, body = c.call("act", "run_saved_command disk_usage")  # real air-gapped actuation
        assert ok and body["lines"], body
        ok, body = c.call("bogus")                             # unknown command -> ok=False, not a crash
        assert ok is False, body
        c.close()
    finally:
        for p in (sock, db):
            os.path.exists(p) and os.remove(p)


def test_event_plane_broadcasts_over_socket() -> None:
    # The daemon pushes unsolicited events; a connected client receives them framed correctly.
    sock = tempfile.mktemp(suffix=".sock")
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    a, b = socket.socketpair()
    try:
        d = Daemon(db_path=db, sock_path=sock)
        d._clients[a] = protocol.LineBuffer()                 # register one end as a "client"
        d._broadcast("alert", {"text": "hello"})
        assert protocol.LineBuffer().feed(b.recv(65536)) == [protocol.event("alert", {"text": "hello"})]
        d._session.close()
    finally:
        a.close(); b.close()
        for p in (sock, db):
            os.path.exists(p) and os.remove(p)


if __name__ == "__main__":
    test_seam_request_response_end_to_end()
    test_event_plane_broadcasts_over_socket()
    print("test_service: OK")
