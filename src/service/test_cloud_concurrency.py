"""ADR-056 Phase 3 — the LIVE-FIRE concurrency check Synapse gated the UI on.

Runs the REAL Daemon (real select() loop, real Session, real unix socket) and triggers a multi-second
cloud inference over the socket. While the cloud is 'thinking', a second client hammers the daemon with
ordinary requests. If the off-loop dispatch is correct, those requests stay prompt — proving the single
select() thread is never blocked, so the Sentinel's sensor fd (drained by that same loop) cannot drop a
frame. No network, no API key: the cloud HTTP is a slow fake injected at the egress seam.
"""
import json
import os
import socket
import tempfile
import threading
import time

import cloud_egress
from cloud_egress import CloudResult
from language.multiplexer import Multiplexer
from service import protocol
from service.server import Daemon
from service.wiring import DemoClaims


def _start_daemon(sock_path, db_path):
    """Construct AND serve the daemon in ONE background thread so every SQLite store is created, used,
    and closed on that same thread (in production, construct+serve are both the main thread)."""
    holder = {}
    def run():
        daemon = Daemon(db_path=db_path, sock_path=sock_path, poll_interval=0.2)
        # No GGUF in CI -> swap in a Multiplexer so the cloud path is exercised (the real product has one
        # when a local model is present). Default dispatch routes to the monkeypatched (slow/failing) seam.
        daemon._session._llm = Multiplexer(DemoClaims())
        holder["daemon"] = daemon
        daemon.serve()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(300):                                   # wait for construction + socket bind
        if os.path.exists(sock_path):
            break
        time.sleep(0.01)
    return thread


def _send(sock, rid, cmd, arg=""):
    sock.sendall(protocol.encode(protocol.request(rid, cmd, arg)))


def _drain(sock, buf, deadline):
    """Read whatever frames are available before `deadline`; return decoded frames."""
    frames = []
    sock.settimeout(max(0.01, deadline - time.monotonic()))
    try:
        data = sock.recv(65536)
        if data:
            frames = buf.feed(data)
    except (socket.timeout, BlockingIOError):
        pass
    return frames


def test_live_daemon_offloop_cloud_does_not_block_the_select_loop(tmp_path, monkeypatch):
    CALL_SECONDS = 1.5

    def slow_openai(req, *, api_key, model="", now=None, transport=None):
        time.sleep(CALL_SECONDS)                    # simulate a heavy cloud inference (releases the GIL)
        return CloudResult(ok=True, text="The cloud considered: " + req.user)
    monkeypatch.setattr(cloud_egress, "openai_complete", slow_openai)

    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")  # AF_UNIX <= ~104 chars
    db_path = str(tmp_path / "jarvis.db")
    server = _start_daemon(sock_path, db_path)

    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); b.connect(sock_path)
    abuf, bbuf = protocol.LineBuffer(), protocol.LineBuffer()
    try:
        # client A kicks off the multi-second cloud call; expect an IMMEDIATE 'thinking' ack
        _send(a, 1, "cloud_ask", {"text": "what is the capital of France?", "key": "sk-test", "provider": "openai"})
        ack = None
        end = time.monotonic() + 1.0
        while ack is None and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.RES and f.get("id") == 1:
                    ack = f
        assert ack is not None and ack["ok"] and ack["body"]["status"] == "thinking"

        # WHILE the cloud is thinking: client B issues rapid status requests. Each must round-trip fast —
        # if the loop were blocked on the cloud call, these would stall ~CALL_SECONDS.
        latencies = []
        probe_end = time.monotonic() + (CALL_SECONDS - 0.4)
        rid = 100
        while time.monotonic() < probe_end:
            rid += 1
            t0 = time.monotonic()
            _send(b, rid, "status")
            got = None
            d = time.monotonic() + 0.5
            while got is None and time.monotonic() < d:
                for f in _drain(b, bbuf, d):
                    if f.get("t") == protocol.RES and f.get("id") == rid:
                        got = f
            assert got is not None, "a status request got NO response while the cloud was thinking (loop blocked)"
            latencies.append(time.monotonic() - t0)
            time.sleep(0.05)

        # the cloud answer eventually arrives as an async event on A
        answer = None
        end = time.monotonic() + 3.0
        while answer is None and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.EVT and f.get("kind") == "cloud_answer":
                    answer = f["body"]

        assert answer is not None and answer["ok"], "cloud_answer event never arrived"
        assert "capital of France" in answer["text"] and answer["provider"] == "openai"
        # THE proof: every concurrent request stayed prompt throughout the in-flight cloud call.
        assert latencies, "no probes ran"
        print(f"\n[concurrency] {len(latencies)} concurrent status RTTs during a {CALL_SECONDS}s cloud call: "
              f"max={max(latencies)*1000:.1f}ms median={sorted(latencies)[len(latencies)//2]*1000:.1f}ms")
        assert max(latencies) < 0.25, f"select loop stalled during cloud call: max status RTT {max(latencies):.3f}s"
        assert len(latencies) >= 5, f"too few probes ({len(latencies)}) to trust the result"
        # Gate-1 instrument: the daemon's own loop-stall meter must agree the loop stayed live (<< the
        # 1.5s call). This is the number the LIVE smoke test reports for real-network/real-sensor.
        gap = answer.get("loop_max_gap_ms")
        assert gap is not None and gap < 300, f"Gate-1 meter shows a loop stall: {gap} ms"
    finally:
        _send(a, 999, "shutdown")
        time.sleep(0.3)
        a.close(); b.close()
        server.join(timeout=3.0)


def test_live_cloud_failure_becomes_recovery_event_not_a_crash(tmp_path, monkeypatch):
    def failing_openai(req, *, api_key, model="", now=None, transport=None):
        return CloudResult(ok=False, kind="rate_limit", error="Rate-limited — wait and retry.")
    monkeypatch.setattr(cloud_egress, "openai_complete", failing_openai)

    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    db_path = str(tmp_path / "j.db")
    server = _start_daemon(sock_path, db_path)

    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        _send(a, 1, "cloud_ask", {"text": "hello", "key": "sk-x", "provider": "openai"})
        answer = None
        end = time.monotonic() + 3.0
        while answer is None and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.EVT and f.get("kind") == "cloud_answer":
                    answer = f["body"]
        assert answer is not None and answer["ok"] is False
        assert answer["kind"] == "rate_limit" and "Rate-limited" in answer["error"]   # recovery, no crash
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


def test_cloud_answer_feeds_the_local_vault(tmp_path, monkeypatch):
    """The Dual-Brain thesis: a cloud insight becomes PERMANENT local symbolic memory. One fake serves
    both legs of the pipeline — prose for the answer (no schema), claims JSON for the extraction (schema
    set) — and we assert the extracted belief is queryable from the LOCAL ONA afterward."""
    def fake(req, *, api_key, model="", now=None, transport=None):
        if req.json_schema is not None:                         # phase 2: extraction leg (firewalled)
            return CloudResult(ok=True, text=json.dumps({"claims": [
                {"type": "RelationClaim", "subject": "Solana", "verb": "IsA", "object": "blockchain"}]}))
        return CloudResult(ok=True, text="Solana is a blockchain.")   # phase 1: the answer leg
    monkeypatch.setattr(cloud_egress, "openai_complete", fake)

    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    db_path = str(tmp_path / "j.db")
    server = _start_daemon(sock_path, db_path)
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        _send(a, 1, "cloud_ask", {"text": "what is Solana?", "key": "sk-x", "provider": "openai"})
        answer, learned = None, None
        end = time.monotonic() + 4.0
        while (answer is None or learned is None) and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.EVT and f.get("kind") == "cloud_answer": answer = f["body"]
                if f.get("t") == protocol.EVT and f.get("kind") == "cloud_learned": learned = f["body"]
        assert answer is not None and answer["ok"], "no cloud answer"
        # the cloud's claim became a committed local belief (tell() returned True -> L1 ONA + L2 store)
        assert learned is not None and learned["count"] >= 1, "cloud did not feed the vault"
        # atom() normalizes terms to lowercase -> '<solana --> blockchain>.'
        assert any("solana" in n.lower() and "blockchain" in n.lower() for n in learned["narsese"]), learned["narsese"]

        # PERSISTENCE proof: query the LOCAL vault for the cloud-taught fact (no cloud involved).
        rid, got = 2, None
        end = time.monotonic() + 3.0
        _send(a, rid, "ask", "<solana --> blockchain>?")
        while got is None and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.RES and f.get("id") == rid: got = f
        assert got is not None and got["ok"]
        assert "no answer in memory" not in (got["body"].get("text") or ""), got["body"]
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)
