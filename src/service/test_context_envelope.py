"""Sprint 5 (context envelope): a paste too long for the on-device window is chunked via the SummaryJob
pipeline when it asks for a summary, and surfaces an honest overflow message otherwise — never the
misleading 'couldn't read that as a question' grounded fallback."""
import os

from service.session import Session


def test_long_summary_paste_autoroutes_to_activity(tmp_path) -> None:
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        long = "summarize this for me: " + ("the contract clause provides for indemnification. " * 1200)
        assert len(long) // 4 > 8192 - 2048, "fixture must be over-length"
        ok, body = s._begin_converse(long, voice=False)
        assert ok and "Activity" in body["text"] and "k tokens" in body["text"], body
        sf = [r for r in s._overnight_queue.list_all() if r["action"] == "summarize_file"]
        assert len(sf) == 1, "the long paste must be enqueued as exactly one summarize_file task"
        assert os.path.exists(sf[0]["arg"]), "the paste must be staged to a temp file on disk"
        assert "indemnification" in open(sf[0]["arg"], encoding="utf-8").read()
    finally:
        s.close()


def test_overflow_decode_yields_honest_message_not_grounded_fallback(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        s._converse_pending[1] = {"state": {}, "question": "q", "voice": False}
        # simulate the LocalBrain returning the llama.cpp context-overflow error
        s._localbrain.results = lambda: [(1, False, "Requested tokens (9000) exceed context window of 8192")]
        s._drain_converse()
        la = [b for k, b in events if k == "local_answer"]
        assert la, "an overflow must still emit a local_answer"
        assert "too long for the on-device model" in la[0]["text"], la[0]["text"]
        assert "9k tokens" in la[0]["text"]                    # parsed from the model's own error
        assert "couldn't read that" not in la[0]["text"].lower()   # NOT the misleading grounded fallback
    finally:
        s.close()


def test_short_or_intentless_paste_is_not_autorouted(tmp_path) -> None:
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        s._begin_converse("summarize this short note", voice=False)          # short + intent -> fits, no route
        long_no_intent = "what does this mean? " + ("legal text without the magic verb. " * 1200)  # long, no verb
        s._begin_converse(long_no_intent, voice=False)
        assert not any(r["action"] == "summarize_file" for r in s._overnight_queue.list_all())
    finally:
        s.close()
