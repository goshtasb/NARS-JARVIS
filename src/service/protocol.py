"""Wire protocol for the headless JARVIS daemon — pure, I/O-free, the IPC contract itself.

Line-delimited JSON: one frame per `\n`-terminated line. Three frame kinds, so the same socket
carries both a request/response plane and an unsolicited server->client event plane:

  request   {"t":"req","id":<int>,"cmd":<str>,"arg":<any>}     client -> daemon
  response  {"t":"res","id":<int>,"ok":<bool>,"body":<any>}    daemon -> client (correlated by id)
  event     {"t":"evt","kind":<str>,"body":<any>}              daemon -> client (push, no id)

Keeping this a pure codec (no sockets here) is what lets us test the contract — and every command
the brain understands — without a process, a socket, or a GUI. See ARCHITECTURE notes in README.
"""
from __future__ import annotations

import json

REQ, RES, EVT = "req", "res", "evt"


def request(rid: int, cmd: str, arg: object = "") -> dict:
    return {"t": REQ, "id": rid, "cmd": cmd, "arg": arg}


def response(rid: int, ok: bool, body: object = None) -> dict:
    return {"t": RES, "id": rid, "ok": ok, "body": body}


def event(kind: str, body: object = None) -> dict:
    return {"t": EVT, "kind": kind, "body": body}


def encode(frame: dict) -> bytes:
    """Serialize one frame to a single newline-terminated line of bytes."""
    return (json.dumps(frame, separators=(",", ":")) + "\n").encode()


class LineBuffer:
    """Accumulates raw bytes and yields complete decoded frames, one per newline.

    Tolerates partial reads (a frame split across two recv()s) and multiple frames in one read.
    Malformed lines raise on decode — the caller decides whether to drop the peer; we never guess.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[dict]:
        self._buf += chunk
        frames: list[dict] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if line:
                frames.append(json.loads(line.decode()))
        return frames
