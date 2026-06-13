"""v1.24.0 Sprint 3: the off-loop Narsese distillation. The expensive extraction runs in a LearnJob
worker; the daemon commits the returned statements to L1 ONA + L2 store on the main thread. These tests
fake the worker (the same way the file-eval tests do) and prove the commit path: result -> tell -> vault."""
from service.session import Session


class _FakeLearnJob:
    """Stand-in for the LearnJob worker — returns canned (tag, payload) events without spawning a model."""
    def __init__(self, narsese):
        self._events = [("result", narsese), ("eof", None)]
    def fileno(self):
        return -1
    def read(self):
        evs, self._events = self._events, [("eof", None)]
        return evs
    def cleanup(self):
        pass


def test_distilled_beliefs_are_committed_to_the_vault(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        # the real worker emits claims_to_narsese() output — judgments with a trailing '.'
        fake = _FakeLearnJob(["<solana --> blockchain>.", "<solana --> fast>."])
        s._learn_jobs[fake.fileno()] = {"job": fake, "narsese": [], "source": "/notes/crypto.md"}
        s._read_learn_job(fake.fileno())
        learned = [b for k, b in events if k == "learned"]
        assert learned and learned[0]["count"] == 2, learned
        assert "<solana --> blockchain>." in learned[0]["narsese"]
        assert learned[0]["source"] == "/notes/crypto.md"
        # the proof: the distilled belief is now queryable from the persistent vault (L1/L2), not just text
        assert s._jarvis.ask("<solana --> blockchain>?") is not None
        assert fake.fileno() not in s._learn_jobs            # reaped after eof
    finally:
        s.close()


def test_empty_extraction_commits_nothing(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        fake = _FakeLearnJob([])                              # the summary asserted no factual claims
        s._learn_jobs[fake.fileno()] = {"job": fake, "narsese": [], "source": "/x.md"}
        s._read_learn_job(fake.fileno())
        assert not [b for k, b in events if k == "learned"]   # no event, no spurious beliefs
    finally:
        s.close()
