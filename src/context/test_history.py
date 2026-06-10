"""ADR-041 sliding conversation window: bounded, session-gapped, render-only. Fake clock, no model."""
from context import history
from context.history import ConversationBuffer


def _clock(t: dict):
    return lambda: t["now"]


def test_observe_then_render_is_a_transcript() -> None:
    t = {"now": 0.0}
    buf = ConversationBuffer(clock=_clock(t))
    assert buf.render() == ""                                          # empty -> no block at all
    buf.observe("what's 17 plus 5?", "22.")
    out = buf.render()
    assert out.startswith("RECENT CONVERSATION")
    assert "User: what's 17 plus 5?" in out and "JARVIS: 22." in out
    assert "NOT durable memory" in out                                 # teaches the model its scope


def test_window_keeps_only_the_last_three_exchanges() -> None:
    t = {"now": 0.0}
    buf = ConversationBuffer(clock=_clock(t))
    for i in range(5):
        buf.observe(f"q{i}", f"a{i}")
    out = buf.render()
    assert "q0" not in out and "a1" not in out                         # oldest evicted
    assert "q2" in out and "q3" in out and "q4" in out and "a4" in out
    assert out.count("User:") == 3 and out.count("JARVIS:") == 3       # MAX_MESSAGES = 6


def test_long_messages_are_truncated_not_dropped() -> None:
    t = {"now": 0.0}
    buf = ConversationBuffer(clock=_clock(t))
    buf.observe("x" * 1000, "y" * 2000)
    out = buf.render()
    assert "x" * history.USER_CAP not in out                           # capped with ellipsis
    assert "…" in out
    assert len(out) < history.USER_CAP + history.ASSISTANT_CAP + 400   # bounded block


def test_session_gap_ends_the_conversation_lazily() -> None:
    t = {"now": 0.0}
    buf = ConversationBuffer(clock=_clock(t))
    buf.observe("first question", "first answer")
    t["now"] = history.SESSION_GAP_SECONDS - 1                         # within the window
    assert "first question" in buf.render()
    t["now"] = history.SESSION_GAP_SECONDS + 2                         # silence exceeded the gap
    assert buf.render() == ""                                          # conversation is over
    buf.observe("new topic", "fresh start")                            # next turn starts clean
    out = buf.render()
    assert "new topic" in out and "first question" not in out


def test_clear_is_immediate_and_total() -> None:
    t = {"now": 0.0}
    buf = ConversationBuffer(clock=_clock(t))
    buf.observe("secret-ish chatter", "ack")
    buf.clear()
    assert buf.render() == ""


if __name__ == "__main__":
    test_observe_then_render_is_a_transcript()
    test_window_keeps_only_the_last_three_exchanges()
    test_long_messages_are_truncated_not_dropped()
    test_session_gap_ends_the_conversation_lazily()
    test_clear_is_immediate_and_total()
    print("context/test_history: OK")
