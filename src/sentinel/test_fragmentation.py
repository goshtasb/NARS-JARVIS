"""Dual-plane fragmentation funnel — deterministic, clock-injected (synthetic timestamps, no sleep)."""
from sentinel.fragmentation import FRAGMENTATION_LADDER, RingState, rate, record
from sentinel.schmitt import DiscState, step


def test_ring_counts_within_window() -> None:
    s = RingState()
    for t in (0.0, 1.0, 2.0, 3.0):           # 4 micro-switches in 3s — full fidelity, no debounce
        s = record(s, t, window=100.0)
    assert rate(s, 3.0, window=100.0) == 4
    assert rate(s, 200.0, window=100.0) == 0  # window has rolled past all of them


def test_ring_boundary_is_exclusive() -> None:
    s = record(RingState(), 0.0, window=10.0)
    assert rate(s, 10.0, window=10.0) == 0     # event exactly at now-window is dropped
    assert rate(s, 9.999, window=10.0) == 1


def test_record_evicts_on_insert() -> None:
    s = record(RingState((0.0, 1.0, 2.0)), 150.0, window=100.0)
    assert s.times == (150.0,)                 # 0,1,2 evicted (older than window), 150 kept


def test_schmitt_rate_transitions_are_deterministic() -> None:
    # Synthetic rate sequence -> asserted attention-level crossings. No clock, no sleep.
    st, emitted = DiscState(), []
    for r in (1, 1, 12, 12, 25, 25, 0, 0):     # focused -> fragmented -> thrashing -> focused
        st, e = step(FRAGMENTATION_LADDER, st, float(r))
        if e:
            emitted.append(e)
    assert emitted == ["fragmented", "thrashing", "focused"], emitted


if __name__ == "__main__":
    test_ring_counts_within_window()
    test_ring_boundary_is_exclusive()
    test_record_evicts_on_insert()
    test_schmitt_rate_transitions_are_deterministic()
    print("sentinel/test_fragmentation: OK")
