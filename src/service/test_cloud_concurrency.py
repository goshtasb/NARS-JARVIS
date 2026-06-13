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


def _start_daemon(sock_path, db_path, local_llm=None):
    """Construct AND serve the daemon in ONE background thread so every SQLite store is created, used,
    and closed on that same thread (in production, construct+serve are both the main thread)."""
    holder = {}
    def run():
        daemon = Daemon(db_path=db_path, sock_path=sock_path, poll_interval=0.2)
        # No GGUF in CI -> swap in a Multiplexer so the cloud path is exercised (the real product has one
        # when a local model is present). Default dispatch routes to the monkeypatched (slow/failing) seam.
        daemon._session._llm = Multiplexer(DemoClaims())
        if local_llm is not None:    # ADR-057: drop a fake LOCAL model into the serializer + point Tier-2 at it
            daemon._session._localbrain._llm = local_llm
            daemon._session._jarvis._assistant = daemon._session._localbrain
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


def _req(sock, buf, rid, cmd, arg, timeout=6.0):
    _send(sock, rid, cmd, arg)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for f in _drain(sock, buf, end):
            if f.get("t") == protocol.RES and f.get("id") == rid:
                return f
    return None


def _wait_event(sock, buf, kind, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for f in _drain(sock, buf, end):
            if f.get("t") == protocol.EVT and f.get("kind") == kind:
                return f["body"]
    return None


def test_recall_offloop_grounds_via_event_without_blocking_the_loop(tmp_path):
    """Gate 2 / Commit 2: the Stage-4 derivation runs OFF-LOOP. The handler returns a fast ack (Stages 0-3
    only); the select loop stays responsive while the worker derives; the grounded answer + STAMP arrives
    as a `recall_result` event. (`a` = recall+events, `b` = concurrent probes — separate sockets so a
    status request can't swallow the event.)"""
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); b.connect(sock_path)
    abuf, bbuf = protocol.LineBuffer(), protocol.LineBuffer()
    try:
        assert _req(a, abuf, 1, "tell", "<solana --> timeout>.")["ok"]
        assert _req(a, abuf, 2, "tell", "<timeout --> dropped_tx>.")["ok"]
        # the handler returns a FAST ack — Stages 0-3 only, never waiting on the ONA derivation
        t0 = time.monotonic()
        ack = _req(a, abuf, 3, "recall", "Why did Solana cause dropped_tx?", timeout=2.0)
        ack_ms = (time.monotonic() - t0) * 1000
        assert ack is not None and ack["ok"] and ack["body"]["status"] == "reasoning", ack
        # while the worker derives off-loop, the loop answers concurrent traffic promptly
        latencies = []
        for rid in range(100, 112):
            s = time.monotonic(); got = _req(b, bbuf, rid, "status", "", timeout=1.0)
            assert got is not None, "status stalled while a Stage-4 worker was in flight"
            latencies.append(time.monotonic() - s)
        # the grounded answer arrives asynchronously
        evt = _wait_event(a, abuf, "recall_result", timeout=6.0)
        assert evt is not None and evt["grounded"] is True, evt
        assert evt["answer"] == "<solana --> dropped_tx>", evt
        assert {p["narsese"] for p in evt["provenance"]} == {"<solana --> timeout>", "<timeout --> dropped_tx>"}
        assert all("learned_at" in p for p in evt["provenance"])
        print(f"\n[recall] handler ack={ack_ms:.1f}ms (Stages 0-3, off-loop); "
              f"concurrent status RTT during derivation: max={max(latencies)*1000:.1f}ms")
        assert ack_ms < 250, f"handler blocked on Stage 4: ack took {ack_ms:.0f}ms"
        assert max(latencies) < 0.25, f"loop stalled during derivation: {max(latencies):.3f}s"
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); b.close(); server.join(timeout=3.0)


def test_gate3_recall_worker_loop_gap_stays_pegged_under_flood(tmp_path):
    """Gate 3 (headless): the daemon's OWN loop-gap meter (now recall-aware) must stay near the poll
    cadence while a Stage-4 worker derives AND a continuous request flood keeps the select loop busy —
    including the pass where the worker flushes its STAMP through the pipe and exits. This is the number
    you'll watch as `loop_max_gap_ms` / `[gate3]` in the live smoke test; here it's driven by socket
    traffic instead of the NSWorkspace sensor."""
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); b.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        assert _req(a, abuf, 1, "tell", "<solana --> timeout>.")["ok"]
        assert _req(a, abuf, 2, "tell", "<timeout --> dropped_tx>.")["ok"]
        ack = _req(a, abuf, 3, "recall", "Why did Solana cause dropped_tx?", timeout=2.0)
        assert ack["body"]["status"] == "reasoning", ack
        # flood the loop with traffic while the worker derives off-loop (stand-in for the sensor load)
        stop = threading.Event()
        def flood():
            rid, lb = 1000, protocol.LineBuffer()
            while not stop.is_set():
                rid += 1; _send(b, rid, "status"); _drain(b, lb, time.monotonic() + 0.02)
        t = threading.Thread(target=flood, daemon=True); t.start()
        evt = _wait_event(a, abuf, "recall_result", timeout=8.0)
        stop.set(); t.join(timeout=1.0)
        assert evt is not None and evt["grounded"] is True, evt        # worker completed off-loop
        gap = evt.get("loop_max_gap_ms")
        print(f"\n[gate3] recall grounded under flood; daemon loop_max_gap_ms = {gap} ms (poll=200ms)")
        assert gap is not None and gap < 50, f"loop stalled while the worker was in flight: {gap} ms"
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); b.close(); server.join(timeout=3.0)


def test_recall_records_compounding_metrics_end_to_end(tmp_path):
    """ADR-056 §8: a live grounded recall records a content-free metric row; the `metrics` command then
    computes FA-LGR + stamp-age from it. Proves the instrumentation is wired through the real daemon."""
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        assert _req(a, abuf, 1, "tell", "<solana --> timeout>.")["ok"]
        assert _req(a, abuf, 2, "tell", "<timeout --> dropped_tx>.")["ok"]
        assert _req(a, abuf, 3, "recall", "Why did Solana cause dropped_tx?", timeout=2.0)["body"]["status"] == "reasoning"
        evt = _wait_event(a, abuf, "recall_result", timeout=6.0)
        assert evt is not None and evt["grounded"] is True, evt
        s = _req(a, abuf, 4, "metrics", "", timeout=2.0)["body"]
        assert s["queries"] >= 1 and s["topics"] >= 1, s
        assert s["fa_lgr"] == 1.0, s                                   # the one topic grounded on first ask
        assert s["stamp_age_median_days"] is not None and s["stamp_age_median_days"] >= 0.0, s
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


def test_recall_hard_timeout_kills_worker_and_escalates(tmp_path, monkeypatch):
    """The time-bomb: a worker that doesn't answer within the ceiling is SIGKILL'd and the query escalates
    to Cloud — no hang. Forced by shrinking the ceiling below the worker's spawn time."""
    monkeypatch.setattr("service.recall_job.TIMEOUT_S", 0.05)   # 50ms < ONA spawn -> always times out
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        assert _req(a, abuf, 1, "tell", "<solana --> timeout>.")["ok"]
        assert _req(a, abuf, 2, "tell", "<timeout --> dropped_tx>.")["ok"]
        ack = _req(a, abuf, 3, "recall", "Why did Solana cause dropped_tx?", timeout=2.0)
        assert ack["body"]["status"] == "reasoning", ack       # a worker DID spawn
        evt = _wait_event(a, abuf, "recall_result", timeout=3.0)
        assert evt is not None and evt["grounded"] is False and evt["escalate"] == "cloud", evt   # killed -> escalate
        # the daemon is still alive and responsive after the SIGKILL+reap
        assert _req(a, abuf, 4, "status", "", timeout=2.0) is not None
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


def test_recall_abstains_and_escalates_when_local_memory_is_empty(tmp_path):
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    buf = protocol.LineBuffer()
    try:
        r = _req(a, buf, 1, "recall", "Why did Ethereum halt Staking?", timeout=8.0)
        assert r is not None and r["ok"], r
        body = r["body"]
        assert body.get("grounded") is False and body.get("escalate") == "cloud", body   # clean escalation
        assert "Ask Cloud" in body.get("text", "")
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


def test_cloud_answer_feeds_the_local_vault(tmp_path, monkeypatch):
    """The Dual-Brain thesis: a cloud insight becomes PERMANENT local symbolic memory. One fake serves
    both legs of the pipeline — prose for the answer (no schema), claims JSON for the extraction (schema
    set) — and we assert the extracted belief is queryable from the LOCAL ONA afterward."""
    def fake(req, *, api_key, model="", now=None, transport=None):
        if req.json_schema is not None:                         # phase 2: extraction leg (firewalled)
            return CloudResult(ok=True, text=json.dumps({
                "claims": [{"type": "RelationClaim", "subject": "Solana", "verb": "IsA", "object": "blockchain"}],
                "aliases": [{"surface": "SOL", "canonical": "solana"}]}))   # extractor yields the alias it used
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

        # Gate 2: the ingest left a PERMANENT, non-empty footprint in the L2 lexicon — terms (via
        # tell()->sink) AND the harvested alias (SOL -> solana). No cloud involved in the lookup.
        rid, lex = 3, None
        end = time.monotonic() + 2.0
        _send(a, rid, "lexicon_stats", "SOL")
        while lex is None and time.monotonic() < end:
            for f in _drain(a, abuf, end):
                if f.get("t") == protocol.RES and f.get("id") == rid: lex = f["body"]
        assert lex is not None and lex["term_count"] >= 2, lex          # solana + blockchain at minimum
        assert lex["resolved"] == "solana", lex                          # the alias SOL -> solana resolved
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


# ── On-device (private) file evaluation: the local summarizer routed to chat, OFF the loop ──
class _FakeSummaryJob:
    """Stand-in for the real CPU summary worker (which would load a GGUF in a subprocess). Uses a real
    pipe fd so the daemon's select() actually wakes on it, then hands back a canned [result]+[eof]. Proves
    the file_summarize -> off-loop fd -> file_result-event plumbing without a model."""
    def __init__(self, file_path, task_id, action="summarize_file"):
        self.file_path = file_path
        self._r, self._w = os.pipe()
        self._done = False
        # Wake the loop slightly AFTER the ack is delivered. The real worker takes seconds (model load +
        # summarize), so the event never races the ack in production; an instant fake would, and `_req`
        # draining the ack would swallow the same-chunk event. The delay makes the ordering deterministic.
        threading.Timer(0.2, self._wake).start()

    def _wake(self):
        try:
            os.write(self._w, b"x")                          # make the read-end selectable
        except OSError:
            pass

    def fileno(self):
        return self._r

    def read(self):
        if self._done:
            return [("eof", None)]
        self._done = True
        name = os.path.basename(self.file_path)
        return [("result", f"Summary of {name}: the document is about testing."), ("eof", None)]

    def cleanup(self):
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass


def test_file_summarize_runs_on_device_and_returns_a_chat_result(tmp_path, monkeypatch):
    """Private-mode file eval: an attached document is summarized by the LOCAL worker (never the cloud)
    and the summary comes back as a `file_result` event. The daemon thread imports SummaryJob at call
    time, so patching the module attribute swaps in the fake across the thread boundary."""
    import service.summary_job
    monkeypatch.setattr(service.summary_job, "SummaryJob", _FakeSummaryJob)
    doc = tmp_path / "report.txt"
    doc.write_text("hello world, this is a test document.")

    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        # a missing file is an honest, immediate failure — never a silent "nothing returned"
        miss = _req(a, abuf, 1, "file_summarize", {"path": str(tmp_path / "nope.txt")}, timeout=2.0)
        assert miss is not None and miss["ok"] is False, miss

        # a real file: fast ack (off-loop), then the summary arrives as an event
        ack = _req(a, abuf, 2, "file_summarize", {"path": str(doc)}, timeout=2.0)
        assert ack is not None and ack["ok"] and ack["body"]["status"] == "reading", ack
        assert ack["body"]["name"] == "report.txt", ack

        evt = _wait_event(a, abuf, "file_result", timeout=5.0)
        assert evt is not None and evt["ok"] is True, evt
        assert evt["name"] == "report.txt", evt
        assert "Summary of report.txt" in evt["text"], evt
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); server.join(timeout=3.0)


# ── ADR-057: Tier-2 local decode runs OFF the select() loop (the 8s-beachball fix) ──
class _SlowLocalLLM:
    """Stand-in for the local 7B: a `generate_text` that takes ~real-decode time. Proves the daemon stays
    responsive while it runs. (DemoClaims has no generate_text, so without this the Tier-2 path would
    answer synchronously from the grounded fallback and there'd be nothing to measure.)"""
    def __init__(self, delay):
        self._delay = delay

    def generate_text(self, system, user, max_tokens=512):
        time.sleep(self._delay)                      # llama.cpp releases the GIL during decode; sleep does too
        return "Here is a Python function that sorts the hashes in place."


def test_tier2_local_decode_runs_offloop_without_blocking_the_loop(tmp_path):
    """The reported P0: a general/coding question falls to the local 7B, whose 512-token decode used to
    lock the main thread for ~8s (beachball). ADR-057 moves the decode to the LocalBrain worker thread;
    the handler returns a fast `thinking_local` ack, the loop answers concurrent traffic the whole time,
    and the answer arrives as a `local_answer` event carrying the worst loop gap it measured."""
    sock_path = os.path.join(tempfile.mkdtemp(prefix="jx", dir="/tmp"), "j.sock")
    server = _start_daemon(sock_path, str(tmp_path / "j.db"), local_llm=_SlowLocalLLM(1.5))
    a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); a.connect(sock_path)
    b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); b.connect(sock_path)
    abuf = protocol.LineBuffer()
    try:
        # the handler returns a FAST ack — prompt assembly only, never waiting on the 1.5s decode
        t0 = time.monotonic()
        ack = _req(a, abuf, 1, "ask", "write me a python script to sort these hashes", timeout=2.0)
        ack_ms = (time.monotonic() - t0) * 1000
        assert ack is not None and ack["ok"] and ack["body"]["status"] == "thinking_local", ack
        assert ack_ms < 250, f"handler blocked on the decode: ack took {ack_ms:.0f}ms"
        # flood the loop with continuous traffic while the 7B decodes off-loop (stand-in for the sensor
        # load). The gap meter is only meaningful UNDER load: idle, the loop legitimately sleeps in
        # select() up to poll=200ms; under traffic it must stay near the cadence, proving no CPU block.
        stop = threading.Event()
        def flood():
            rid, lb = 1000, protocol.LineBuffer()
            while not stop.is_set():
                rid += 1; _send(b, rid, "status"); _drain(b, lb, time.monotonic() + 0.02)
        t = threading.Thread(target=flood, daemon=True); t.start()
        evt = _wait_event(a, abuf, "local_answer", timeout=6.0)
        stop.set(); t.join(timeout=1.0)
        assert evt is not None and "sorts the hashes" in evt["text"], evt   # decode completed off-loop
        gap_ms = evt["loop_max_gap_ms"]
        print(f"\n[tier2] handler ack={ack_ms:.1f}ms (off-loop); local 7B decoded under flood; "
              f"daemon loop_max_gap_ms = {gap_ms} ms (poll=200ms)")
        assert gap_ms is not None and gap_ms < 50, f"select loop stalled during the decode: {gap_ms}ms"
    finally:
        _send(a, 999, "shutdown"); time.sleep(0.3); a.close(); b.close(); server.join(timeout=3.0)
