"""Outbound voice — give ONA's verdict natural language WITHOUT authority over truth.

The LLM never sees the question; it only rephrases a verdict ONA already decided. Truth->certainty
bands and the polarity-correct statement are computed HERE in code. The formatter is a discardable
cosmetic layer: any deviation (a hallucinated content word, a dropped certainty marker, over-length)
is silently replaced by the deterministic template. The literal "(confidence; based on:)" trail is
appended verbatim, always — ground truth is visible no matter what the prose does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Ratified V2 baseline bands — tuned in code, NEVER graded by the model.
_CONF_CONFIDENT, _CONF_LIKELY, _CONF_TENTATIVE = 0.85, 0.70, 0.50
_FREQ_AFFIRM, _FREQ_NEGATE = 0.80, 0.20

UNKNOWN_ANSWER = "I don't know — I have no basis for that in what I've learned."


class Polarity(Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class Band(Enum):
    CONFIDENT = "confident"
    LIKELY = "likely"
    TENTATIVE = "tentative"
    GUESS = "guess"


# Hedge markers the formatter MUST retain (any one) so it cannot silently UPGRADE the certainty.
# Confident needs none. Every marker word is itself a stopword, so it never trips the content check.
_MARKER = {
    Band.CONFIDENT: (),
    Band.LIKELY: ("probabl", "likely"),
    Band.TENTATIVE: ("tentativ", "limited", "not sure", "unsure"),
    Band.GUESS: ("guess", "barely", "weak"),
}

_LEAD = {
    (Polarity.YES, Band.CONFIDENT): "Yes",
    (Polarity.YES, Band.LIKELY): "Probably yes",
    (Polarity.YES, Band.TENTATIVE): "Tentatively, yes",
    (Polarity.YES, Band.GUESS): "Possibly yes, but it's barely a guess",
    (Polarity.NO, Band.CONFIDENT): "No",
    (Polarity.NO, Band.LIKELY): "Probably not",
    (Polarity.NO, Band.TENTATIVE): "Tentatively, no",
    (Polarity.NO, Band.GUESS): "Possibly not, but it's barely a guess",
}


def assess(frequency: float, confidence: float) -> tuple[Polarity, Band]:
    """Map ONA's (frequency, confidence) onto fixed polarity + certainty bands. Pure."""
    polarity = (Polarity.YES if frequency >= _FREQ_AFFIRM
                else Polarity.NO if frequency <= _FREQ_NEGATE
                else Polarity.UNCLEAR)
    band = (Band.CONFIDENT if confidence >= _CONF_CONFIDENT
            else Band.LIKELY if confidence >= _CONF_LIKELY
            else Band.TENTATIVE if confidence >= _CONF_TENTATIVE
            else Band.GUESS)
    return polarity, band


def _lead(polarity: Polarity, band: Band) -> str:
    if polarity is Polarity.UNCLEAR:
        return "It's unclear — the evidence is mixed"
    return _LEAD[(polarity, band)]


@dataclass(frozen=True)
class Verdict:
    polarity: Polarity
    band: Band
    statement: str           # polarity-correct English, e.g. "Tim is a bird" / "Tim is not a bird"
    confidence: float
    frequency: float
    evidence: list[str]      # real premise English (or terms), from the ONA stamp


def _trail(v: Verdict) -> str:
    ev = "; ".join(v.evidence) if v.evidence else "no recorded premises"
    return f"(confidence {v.confidence:.2f}; based on: {ev})"


def deterministic_answer(v: Verdict) -> str:
    """The always-correct template — ground truth, zero model involvement."""
    return f"{_lead(v.polarity, v.band)} — {v.statement}. {_trail(v)}"


VOICE_SYSTEM_PROMPT = (
    "You are a read-aloud formatter, NOT an assistant. You are given a VERDICT that is already "
    "decided and EVIDENCE already gathered. Rephrase them into ONE short, natural sentence. "
    "You MUST NOT add any fact, name, reason, number, or qualifier that is not in the input. "
    "You MUST keep the verdict's certainty wording (do not strengthen or weaken it). "
    "Do NOT use your own knowledge. Output only the sentence."
)


def _sheet(v: Verdict) -> str:
    return (f"VERDICT: {_lead(v.polarity, v.band)}\n"
            f"STATEMENT: {v.statement}\n"
            f"EVIDENCE: {v.evidence}")


_WORD = re.compile(r"[a-z0-9]+")
# Function/verdict words the formatter may freely use; only CONTENT words are checked vs the sheet.
_STOP = frozenset(
    "a an the is are am was were be been not no yes probably tentatively tentative likely maybe "
    "possibly guess unclear mixed barely it that this these those i you we they think believe know "
    "known my your our of on in to with from based because but and or so do does did have has had "
    "confidence evidence premise premises sure unsure limited weak about as re ll ve".split()
)


def _content_words(text: str) -> list[str]:
    # length-1 tokens are contraction fragments ('I'm'->'m') / noise, never content nouns.
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1 and w not in _STOP]


def sanitize_voice(text: object, v: Verdict, max_len: int = 220) -> str | None:
    """Ruthless: reject (->None) if the prose adds a content word absent from the verdict/evidence,
    drops the required certainty marker, is empty, or is over length. Pure."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t or len(t) > max_len:
        return None
    allowed = set(_WORD.findall((v.statement + " " + " ".join(v.evidence)).lower()))
    for w in _content_words(t):
        if w not in allowed:
            return None  # a noun/content word not in the evidence base => hallucination
    low = t.lower()
    if v.polarity is Polarity.UNCLEAR:
        if not any(m in low for m in ("unclear", "mixed")):
            return None
    else:
        markers = _MARKER[v.band]
        if markers and not any(m in low for m in markers):
            return None  # the hedge was dropped or upgraded
    return t


class Voice:
    """Renders a Verdict to English. Template-authoritative; the formatter LLM is discardable."""

    def __init__(self, formatter: object | None = None) -> None:
        self._fmt = formatter  # duck-typed: .generate_text(system_prompt, user) -> str

    def say(self, v: Verdict) -> str:
        base = deterministic_answer(v)
        if self._fmt is None:
            return base
        try:
            clean = sanitize_voice(self._fmt.generate_text(VOICE_SYSTEM_PROMPT, _sheet(v)), v)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — any model/runtime error -> safe template
            clean = None
        return f"{clean} {_trail(v)}" if clean is not None else base

    @staticmethod
    def say_unknown(message: str = UNKNOWN_ANSWER) -> str:
        return message
