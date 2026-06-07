"""Focus-block tracker + intervention-lift KPI — pure, synthetic timestamps (no clock, no sleep)."""
from sentinel.focusblock import Block, BlockState, close, lift, update


def test_block_opens_and_closes_on_steady_transitions() -> None:
    st = BlockState()
    st, done = update(st, 0.0, True)        # enter steady -> block opens, nothing emitted yet
    assert st.in_block and done is None
    st, done = update(st, 10.0, True)       # still steady -> no transition
    assert done is None
    st, done = update(st, 30.0, False)      # leave steady -> emit the completed 30s block
    assert done == Block(0.0, 30.0) and not st.in_block


def test_close_flushes_open_block_only() -> None:
    st, _ = update(BlockState(), 5.0, True)
    assert close(st, 25.0) == Block(5.0, 20.0)     # open block -> flushed
    assert close(BlockState(), 25.0) is None        # nothing open -> nothing to flush


def test_lift_compares_after_vs_before_accepted() -> None:
    # accepted nudge at t=1000 (window 1800s). before: one 60s block; after: two 300s blocks.
    blocks = [Block(500.0, 60.0), Block(1100.0, 300.0), Block(1500.0, 300.0)]
    k = lift(blocks, [(1000.0, True)])
    assert k["accepted"] == 1
    assert k["pre_median_s"] == 60.0 and k["post_median_s"] == 300.0
    assert k["delta_s"] == 240.0


def test_lift_ignores_declined_and_out_of_window() -> None:
    # both blocks fall outside the +/-1800s window of t=3000; the declined nudge contributes nothing.
    blocks = [Block(0.0, 999.0), Block(5000.0, 999.0)]
    k = lift(blocks, [(3000.0, False), (3000.0, True)])
    assert k["accepted"] == 1
    assert k["pre_median_s"] is None and k["post_median_s"] is None and k["delta_s"] is None


if __name__ == "__main__":
    test_block_opens_and_closes_on_steady_transitions()
    test_close_flushes_open_block_only()
    test_lift_compares_after_vs_before_accepted()
    test_lift_ignores_declined_and_out_of_window()
    print("sentinel/test_focusblock: OK")
