"""Sliding conversational history (ADR-041) — Functional Core (S-02). The short-term memory that
makes follow-up questions work.

Before this, every `converse()` turn was stateless: "what about tomorrow?" arrived with no trace of
the question it follows. This buffer holds the last few exchanges IN MEMORY ONLY and renders them as
a plain context block (the same injection idiom as live context / habits / persona — never raw chat
template tokens, which the llama.cpp chat templater owns).

Deliberately ephemeral, three ways (the PM's "short-term chatter must never hard-bake" rule):
- in-memory deque, gone on daemon restart by construction (no schema, no eviction job, no growth);
- a CONVERSATION boundary: a gap of SESSION_GAP_SECONDS without a turn ends the session lazily at the
  next render/observe — this is NOT the 45 s compute-idle gate (reading one research answer takes
  longer than that; a human pause is not the end of a conversation);
- render-only: nothing here feeds the memory/persona/habit pipelines — those keep reading the raw
  utterance, so transient chatter can never become a durable belief through this path.

Bounded for the 7B's context window: at most MAX_MESSAGES rendered, each truncated.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

MAX_MESSAGES = 6           # 3 user/assistant exchanges — enough for follow-ups, cheap to prefill
USER_CAP = 300             # chars rendered per user message
ASSISTANT_CAP = 600        # chars rendered per assistant message (research answers run long)
SESSION_GAP_SECONDS = 900.0  # 15 min of silence = the conversation is over

_HEADER = ("RECENT CONVERSATION (earlier turns of this session, oldest first — use them to resolve "
           "follow-ups like 'what about that?'; they are context, NOT durable memory):")


def _trim(text: str, cap: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= cap else text[: cap - 1] + "…"


class ConversationBuffer:
    """The sliding window. `clock` injectable for tests; defaults to monotonic time."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._turns: deque[tuple[str, str]] = deque(maxlen=MAX_MESSAGES)  # (role, text)
        self._last_at: float | None = None

    def _expire_if_stale(self) -> None:
        if self._last_at is not None and self._clock() - self._last_at > SESSION_GAP_SECONDS:
            self._turns.clear()

    def observe(self, question: str, reply: str) -> None:
        """Record one completed exchange. A turn arriving after the session gap starts fresh."""
        self._expire_if_stale()
        if (q := (question or "").strip()):
            self._turns.append(("User", _trim(q, USER_CAP)))
        if (r := (reply or "").strip()):
            self._turns.append(("JARVIS", _trim(r, ASSISTANT_CAP)))
        self._last_at = self._clock()

    def render(self) -> str:
        """The context block for the prompt — '' when there is no live conversation."""
        self._expire_if_stale()
        if not self._turns:
            return ""
        return _HEADER + "\n" + "\n".join(f"{role}: {text}" for role, text in self._turns)

    def clear(self) -> None:
        self._turns.clear()
        self._last_at = None
