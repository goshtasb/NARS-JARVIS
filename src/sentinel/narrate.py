"""Surprise narration (C3 / M2). Imperative Shell (LLM) + a PURE action-forbidding sanitizer.

Observation-only: the prompt forbids actions, but we do NOT trust the prompt — a deterministic
sanitizer rejects any action/agency wording (or malformed output) and the narrator falls back to
a generic terminal warning. The sentinel has no execution capability, so even a sanitizer miss is
only text. Built with dependency injection (chaotic fakes) like M0.
"""
from __future__ import annotations

from typing import Callable

from .surprise import SurpriseEvent

NARRATION_SYSTEM_PROMPT = (
    "You are a passive system monitor. Describe the observed anomaly in ONE sentence. "
    "You MUST NOT suggest, recommend, or describe any action, command, or fix. "
    "Format strictly: 'Observed anomaly: <what>. Context: <why it is unusual>. I am monitoring.'"
)

# Conservative action/agency denylist — prefers the safe fallback over emitting agency wording.
_ACTION_MARKERS = (
    "you should", "i recommend", "i suggest", "you can", "let me", "shall i", "i will",
    "run ", "execute", "kill", "delete", "remove ", "sudo", "restart", "reboot", "try ", "^",
)


def sanitize_narration(text: object) -> str | None:
    """Return clean observation text, or None if it suggests an action / is malformed. Pure."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > 500:
        return None
    if any(marker in stripped.lower() for marker in _ACTION_MARKERS):
        return None
    return stripped


class Narrator:
    def __init__(self, llm: object, on_alert: Callable[[str], object] | None = None) -> None:
        self._llm = llm
        self._on_alert = on_alert or (lambda text: print(text))

    def narrate(self, event: SurpriseEvent) -> str:
        try:
            raw = self._llm.generate(NARRATION_SYSTEM_PROMPT, self._context(event))  # type: ignore[attr-defined]
            clean = sanitize_narration(raw)
        except Exception:
            clean = None  # any model/runtime error -> safe fallback, never crash
        text = clean if clean is not None else self._fallback(event)
        self._on_alert(text)
        return text

    @staticmethod
    def _context(event: SurpriseEvent) -> str:
        return (f"term={event.term} surprise={event.surprise:.2f} "
                f"prior_expectation={event.prior_expectation} "
                f"actual_expectation={event.actual_expectation:.2f}")

    @staticmethod
    def _fallback(event: SurpriseEvent) -> str:
        return f"Observed anomaly: {event.term} (surprise={event.surprise:.2f}). I am monitoring."
