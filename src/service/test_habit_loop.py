"""Unit tests for the Habit Brain loop (ADR-026): telemetry feeds + persists, eligibility filters,
and the proposal tick gates correctly + respects the once-per-occurrence cooldown. Stubbed brain/
store/consent — no ONA, no OS."""
from datetime import datetime

from service.habit_loop import HabitLoop

_CLOCK = lambda: datetime(2026, 6, 9, 9, 0).astimezone()   # fixed 9am -> bucket "h09"


class _Truth:
    def __init__(self, f, c):
        self.frequency, self.confidence = f, c


class _Ans:
    def __init__(self, t):
        self.truth = t


class _Brain:
    def __init__(self, truth=None):
        self.added = []
        self._truth = truth
    def add_belief(self, s):
        self.added.append(s)
        return []
    def ask(self, _q):
        return _Ans(self._truth) if self._truth else None


class _Store:
    def __init__(self):
        self.rows: dict = {}
        self.records = []
    def all(self):
        return [(k, r["frequency"], r["confidence"]) for k, r in self.rows.items()]
    def record(self, key, bucket, action, arg, f, c, now=None):
        self.rows[key] = {"key": key, "bucket": bucket, "action": action, "arg": arg,
                          "frequency": f, "confidence": c, "last_proposed": ""}
        self.records.append((key, f, c))
    def for_bucket(self, b):
        return [r for r in self.rows.values() if r["bucket"] == b]
    def list_all(self):
        return list(self.rows.values())
    def delete(self, key):
        self.rows.pop(key, None)
    def mark_proposed(self, key, day):
        self.rows[key]["last_proposed"] = day


class _Consent:
    def __init__(self):
        self.requests = []
    def request(self, **kw):
        self.requests.append(kw)
        return len(self.requests)


def test_observe_feeds_evidence_and_persists() -> None:
    b, s = _Brain(_Truth(1.0, 0.5)), _Store()
    HabitLoop(b, s, _Consent(), lambda a, g: "", clock=_CLOCK).observe("dark_mode", "", "did")
    assert any("{1.0 0.5}" in x for x in b.added)           # YES evidence fed
    assert s.records and s.records[0][0] == "h09_dark_mode"  # persisted under the hour-bucket key


def test_observe_denied_feeds_negative() -> None:
    b = _Brain(_Truth(0.0, 0.9))
    HabitLoop(b, _Store(), _Consent(), lambda a, g: "", clock=_CLOCK).observe("dark_mode", "", "denied")
    assert any("{0.0 0.9}" in x for x in b.added)


def test_observe_ignores_ineligible_actions() -> None:
    b, s = _Brain(), _Store()
    HabitLoop(b, s, _Consent(), lambda a, g: "", clock=_CLOCK).observe("find_file", "x", "did")
    assert b.added == [] and s.records == []                 # read-only never becomes a habit


def test_propose_due_opens_consent_only_when_armed_then_cools_down() -> None:
    s = _Store()
    s.record("h09_dark_mode", "h09", "dark_mode", "", 1.0, 0.9)   # armed (conf 0.9, E 0.95)
    con = _Consent()
    loop = HabitLoop(_Brain(_Truth(1.0, 0.9)), s, con, lambda a, g: "done", clock=_CLOCK)
    loop.propose_due()
    assert len(con.requests) == 1 and con.requests[0]["kind"] == "habit"
    loop.propose_due()                                       # same day-bucket -> cooldown, no re-propose
    assert len(con.requests) == 1


def test_propose_due_silent_when_not_armed() -> None:
    s = _Store()
    s.record("h09_dark_mode", "h09", "dark_mode", "", 1.0, 0.5)   # one confirmation -> E 0.75 < floor
    con = _Consent()
    HabitLoop(_Brain(_Truth(1.0, 0.5)), s, con, lambda a, g: "", clock=_CLOCK).propose_due()
    assert con.requests == []


def test_describe_lists_armed_and_learning_without_raw_math() -> None:
    s = _Store()
    s.record("h09_mute", "h09", "mute", "", 1.0, 0.9)           # armed (E 0.95)
    s.record("h14_dark_mode", "h14", "dark_mode", "", 1.0, 0.5)  # learning (E 0.75)
    out = HabitLoop(_Brain(), s, _Consent(), lambda a, g: "", clock=_CLOCK).describe()
    assert "mute around 9:00 AM — [Armed]" in out
    assert "dark mode around 2:00 PM — [Learning]" in out and "seen ~1×" in out
    assert "0.9" not in out and "conf" not in out               # no raw NARS numbers leak to the user


def test_describe_empty() -> None:
    out = HabitLoop(_Brain(), _Store(), _Consent(), lambda a, g: "", clock=_CLOCK).describe()
    assert "not tracking any habits" in out


def test_forget_craters_and_deletes() -> None:
    s = _Store()
    s.record("h09_mute", "h09", "mute", "", 1.0, 0.9)
    b = _Brain()
    out = HabitLoop(b, s, _Consent(), lambda a, g: "", clock=_CLOCK).forget("mute")
    assert "Forgotten" in out and s.list_all() == []            # purged
    assert any("{0.0 0.9}" in x for x in b.added)               # cratered with absolute negative


def test_forget_no_match() -> None:
    s = _Store()
    s.record("h09_mute", "h09", "mute", "", 1.0, 0.9)
    out = HabitLoop(_Brain(), s, _Consent(), lambda a, g: "", clock=_CLOCK).forget("brightness")
    assert "No habit matches" in out and len(s.list_all()) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("service/test_habit_loop: OK")
