"""ADR-011: the sentinel's earned autonomy + baseline survive a simulated daemon restart, on REAL
ONA. Mirrors test_autonomy's earn-loop, then persists -> new brain -> replays -> asserts the gate
still passes (and the never-consented category still can't)."""
import tempfile

from brain import Brain
from sentinel.intervention import steadiness_belief
from sentinel.store import SentinelStore
from service.autonomy import approved_term, evidence_belief, gate_passes
from service.sentinel_loop import persist_belief, replay_beliefs
from memory import statement_term


def test_earned_autonomy_survives_restart() -> None:
    store = SentinelStore(tempfile.mktemp(suffix=".db"))
    comms = approved_term("comms")
    base = statement_term(steadiness_belief("steady"))

    # ── Session 1: earn autonomy for comms (~6 approvals) + lay down a baseline, write-through. ──
    with Brain(cycles_per_step=20) as b1:
        for _ in range(6):
            b1.add_belief(evidence_belief("comms", True))
        persist_belief(store, b1, comms)
        for _ in range(6):
            b1.add_belief(steadiness_belief("steady"))
        persist_belief(store, b1, base)

        ans = b1.ask(comms + "?")
        assert ans and ans.truth and gate_passes(ans.truth.frequency, ans.truth.confidence)  # earned

    persisted = dict((t, (f, c)) for t, f, c in store.beliefs())
    assert comms in persisted and base in persisted

    # ── Simulated restart: a FRESH ONA knows nothing until we replay from the store. ──
    with Brain(cycles_per_step=20) as b2:
        assert b2.ask(comms + "?") is None                 # blank slate
        n = replay_beliefs(store, b2)
        assert n == 2
        ans = b2.ask(comms + "?")
        assert ans and ans.truth and gate_passes(ans.truth.frequency, ans.truth.confidence), \
            "earned autonomy must survive the restart"     # THE invariant
        # the baseline term is back too (its truth replayed)
        bans = b2.ask(base + "?")
        assert bans is not None and bans.truth is not None


def test_unconsented_category_stays_locked_after_restart() -> None:
    store = SentinelStore(tempfile.mktemp(suffix=".db"))
    with Brain(cycles_per_step=20) as b1:
        for _ in range(6):
            b1.add_belief(evidence_belief("comms", True))
        persist_belief(store, b1, approved_term("comms"))
    with Brain(cycles_per_step=20) as b2:
        replay_beliefs(store, b2)
        assert b2.ask(approved_term("media") + "?") is None   # never consented -> never autonomous


if __name__ == "__main__":
    test_earned_autonomy_survives_restart()
    test_unconsented_category_stays_locked_after_restart()
    print("service/test_sentinel_persistence: OK")
