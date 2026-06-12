"""ADR-052: the document-summary offload engine. Proves the heavy Map-Reduce runs off the select loop
via a detached worker + stdout line protocol, with NO real model and NO real subprocess in the unit
tests (a fake job / fake LLM stand in)."""
import io
import json
import sys

import dbconn
from actions import documents
from overnight.store import HeldLedger, OvernightQueue
from service.overnight_runner import OvernightRunner
from service.summary_job import SummaryJob


# ── WAL hardening (must precede the engine) ──
def test_dbconn_sets_wal_and_busy_timeout(tmp_path) -> None:
    db = dbconn.connect(str(tmp_path / "t.db"))
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# ── progress hook in the pure summarizer ──
def test_summarize_calls_on_step_per_map_chunk() -> None:
    text = "alpha. " * 400 + "\n\n" + "beta. " * 400      # forces >1 chunk
    steps: list[tuple[int, int]] = []
    documents.summarize(text, lambda s, u, m: "ok", on_step=lambda i, n: steps.append((i, n)))
    assert steps and steps[0][0] == 1                      # 1-based
    assert all(n == steps[-1][1] for _, n in steps)        # stable N
    assert [i for i, _ in steps] == list(range(1, len(steps) + 1))  # contiguous 1..N


# ── the stdout line protocol parser ──
def test_summary_job_parses_protocol_lines() -> None:
    assert SummaryJob._parse(b"[progress] {\"i\": 3, \"n\": 40}") == ("progress", {"i": 3, "n": 40})
    assert SummaryJob._parse(b'[result] "Summarized x"') == ("result", "Summarized x")
    assert SummaryJob._parse(b'[error] "boom"') == ("error", "boom")
    assert SummaryJob._parse(b"random noise") is None      # non-protocol lines ignored


# ── runner offload control flow (fake job: no subprocess) ──
class _FakeJob:
    """Stands in for SummaryJob; replays canned read() batches, never spawns a process."""
    def __init__(self, file, tid, action="summarize_file") -> None:
        self.task_id, self.action, self.arg = tid, action, file
        self.cleaned = False
        self._batches = [[("progress", {"i": 1, "n": 2})],
                         [("progress", {"i": 2, "n": 2})],
                         [("result", "Summarized doc:\n\nthe gist")],
                         [("eof", None)]]

    def fileno(self) -> int:
        return 4242

    def read(self):
        return self._batches.pop(0) if self._batches else [("eof", None)]

    def cleanup(self) -> None:
        self.cleaned = True


class _BoomRunner:
    """ActionRunner stub — proves summarize_file NEVER reaches inline perform() (it must offload)."""
    def perform(self, name, arg=""):
        raise AssertionError(f"summarize_file ran INLINE on the loop: {name} {arg}")


def test_runner_offloads_summarize_and_finalizes_from_worker() -> None:
    q, led = OvernightQueue(":memory:"), HeldLedger(":memory:")
    tid = q.enqueue("summarize_file", "/tmp/doc.pdf")
    events: list[tuple[str, dict]] = []
    runner = OvernightRunner(q, led, _BoomRunner(), lambda k, b: events.append((k, b)), make_job=_FakeJob)

    runner.start()
    runner.advance()                                       # -> offloads (does NOT call perform)
    row = next(r for r in q.list_all() if r["id"] == tid)
    assert row["status"] == "running" and runner.active and runner.extra_fds() == [4242]

    runner.advance()                                       # no-op while the job is in flight
    assert next(r for r in q.list_all() if r["id"] == tid)["status"] == "running"

    fd = runner.extra_fds()[0]
    runner.handle_fd(fd)                                   # progress 1/2 -> live status
    assert "1/2" in next(r for r in q.list_all() if r["id"] == tid)["result"]
    runner.handle_fd(fd)                                   # progress 2/2
    runner.handle_fd(fd)                                   # result -> done
    done = next(r for r in q.list_all() if r["id"] == tid)
    assert done["status"] == "done" and "the gist" in done["result"]
    runner.handle_fd(fd)                                   # eof -> job cleared
    assert runner.extra_fds() == [] and runner._job is None


# ── the worker end-to-end (fake LLM, real file + protocol, no GPU) ──
def test_summary_worker_streams_progress_then_result(tmp_path, monkeypatch, capsys) -> None:
    doc = tmp_path / "note.txt"
    doc.write_text("alpha. " * 400 + "\n\n" + "beta. " * 400)

    class _FakeLLM:
        def generate_text(self, system, user, max_tokens=64):
            return "a section summary"
    import language.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LocalLLM", lambda *a, **k: _FakeLLM())

    from service import summary_worker
    rc = summary_worker.main([str(doc), "7"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[progress] " in out and "[result] " in out
    # the result line is valid JSON carrying the summary text
    result_line = [ln for ln in out.splitlines() if ln.startswith("[result] ")][0]
    payload = json.loads(result_line[len("[result] "):])
    assert payload.startswith("Summarized note.txt")


def test_summary_worker_reports_missing_file_as_error(tmp_path, capsys) -> None:
    from service import summary_worker
    rc = summary_worker.main([str(tmp_path / "nope.pdf"), "1"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("[error] ")          # extraction problem -> terminal error, no model
