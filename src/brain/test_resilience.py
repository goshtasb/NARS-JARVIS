"""ADR-036: the NAR resilience wrapper. A malformed-Narsese / killed NAR must, WITH an on_restart hook,
relaunch + replay + recover; WITHOUT a hook, behaviour is unchanged (the broken pipe propagates)."""
from brain import Brain, BrainUnavailable

_STORE = ["<format_directive --> omit_greeting_prose>. {1.0 0.9}"]


def test_restart_replays_and_recovers_after_a_kill() -> None:
    restarts = {"n": 0}

    def on_restart(b: Brain) -> None:
        restarts["n"] += 1
        for s in _STORE:
            b.add_belief(s)              # re-entrant during restart -> guarded, no nested restart

    b = Brain(cycles_per_step=50, on_restart=on_restart)
    for s in _STORE:
        b.add_belief(s)
    b._proc.kill(); b._proc.wait()       # DURESS: the NAR dies out from under the wrapper
    ans = b.ask("<format_directive --> ?x>?")
    assert restarts["n"] >= 1 and ans is not None and ans.truth is not None   # recovered + replayed
    assert "omit_greeting_prose" in ans.term
    b.close()


def test_no_hook_default_still_propagates() -> None:
    b = Brain(cycles_per_step=50)         # resilience off (no hook) -> unchanged behaviour
    b._proc.kill(); b._proc.wait()
    try:
        b.add_belief("<a --> b>. {1.0 0.9}")
        assert False, "expected the dead pipe to raise"
    except (BrokenPipeError, BrainUnavailable):
        pass


def test_exhausting_restarts_raises_brain_unavailable() -> None:
    # A persistently-broken NAR: kill it before every op so each guarded call must restart; once the
    # restart budget is spent, it raises BrainUnavailable (the caller's fail-closed signal).
    def on_restart(b: Brain) -> None:
        b._proc.kill(); b._proc.wait()    # sabotage the freshly-spawned proc so recovery keeps failing
    b = Brain(cycles_per_step=50, on_restart=on_restart, max_restarts=2)
    b._proc.kill(); b._proc.wait()
    try:
        b.add_belief("<a --> b>. {1.0 0.9}")
        assert False, "expected BrainUnavailable after the restart budget"
    except BrainUnavailable:
        pass


if __name__ == "__main__":
    test_restart_replays_and_recovers_after_a_kill()
    test_no_hook_default_still_propagates()
    test_exhausting_restarts_raises_brain_unavailable()
    print("brain/test_resilience: OK")
