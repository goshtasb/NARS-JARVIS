"""Headless proof of the voice pipeline: a stub whisper.cpp (so no 140MB model needed) is spawned by
the daemon, multiplexed into select(), its transcript routed through the command pipeline, and the
reply emitted as events. Proves the Popen+select+route+TTS-handoff seam without a microphone."""
import os
import socket
import stat
import tempfile
import threading
import time

from service import Client, Daemon


def _make_stub_whisper(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix="-whisper.sh"); os.close(fd)
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\n# emulate whisper-cli: ignore args, print a fixed transcript\necho '{text}'\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _spawn(sock: str, db: str) -> None:
    threading.Thread(target=lambda: Daemon(db_path=db, sock_path=sock, poll_interval=0.1).serve(),
                     daemon=True).start()


def _connect(sock: str) -> Client:
    c = Client(sock)
    for _ in range(200):
        try:
            c.connect(); return c
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.05)
    raise RuntimeError("daemon never came up")


def test_voice_pipeline_transcribes_routes_and_answers() -> None:
    stub = _make_stub_whisper("tell <a --> b>.")          # the "spoken" utterance
    model = tempfile.mktemp(suffix=".bin"); open(model, "w").close()  # whisper_available() needs a model file
    os.environ["NARS_JARVIS_WHISPER"] = stub
    os.environ["NARS_JARVIS_WHISPER_MODEL"] = model
    os.environ["NARS_JARVIS_NO_TTS"] = "1"                 # don't actually play audio in the test
    sock = tempfile.mktemp(suffix=".sock")
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    wav = tempfile.mktemp(suffix=".wav"); open(wav, "w").close()  # the client's "recording"
    try:
        _spawn(sock, db)
        c = _connect(sock)
        events: list[tuple[str, dict]] = []
        c.set_event_handler(lambda kind, body: events.append((kind, body)))
        ok, body = c.call("voice", {"path": wav})
        assert ok and body.get("status") == "transcribing", body
        # the transcript + answer arrive asynchronously as events; pump until we have both
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and {k for k, _ in events} < {"transcript", "answer"}:
            c.pump()
        kinds = {k: b for k, b in events}
        assert "transcript" in kinds and kinds["transcript"]["text"] == "tell <a --> b>.", events
        assert "answer" in kinds and "committed" in kinds["answer"]["text"], events  # routed -> tell -> committed
        assert not os.path.exists(wav), "utterance WAV should be deleted after transcription"
        c.close()
    finally:
        for p in (sock, db, stub, model, wav):
            os.path.exists(p) and os.remove(p)
        for k in ("NARS_JARVIS_WHISPER", "NARS_JARVIS_WHISPER_MODEL", "NARS_JARVIS_NO_TTS"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    test_voice_pipeline_transcribes_routes_and_answers()
    print("test_voice: OK")
