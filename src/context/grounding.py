"""Pre-commit hybrid grounding (ADR-013). Functional Core (S-02) — pure.

Reconciles the conversational memory write-path against the procedural autonomy gate. The split-brain
bug: a casual request ("please auto-hide my IDE") gets auto-saved (ADR-008) as a preference that
contradicts the sentinel's learned authorization in `sentinel_beliefs` — two stores disagree, and the
poisoned memory becomes injectable ground truth.

Resolution: autonomy control belongs to the gate (the single source of truth), NOT to free-form
conversational memory. So a would-be memory that asserts control over a sentinel-governed category is
dropped at pre-commit, and the deterministic layer answers with the authoritative habit state. This is
the synchronous read-before-write shape of `contradiction.ContradictionGuard`, applied across the
conversational↔procedural boundary. `context` must not import `service`, so the expectation floor is
the local copy already used by `context.habits`.
"""
from __future__ import annotations

import re

from memory.slots import slot_of   # the single-valued-slot detector (ADR-009) — pure text fn

from .habits import EXP_FLOOR, _FRIENDLY, _expectation

# Category synonyms (reverse of habits._FRIENDLY) — the words a user might use for a governed bucket.
_CATEGORY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "dev": ("ide", "developer", "dev tools", "dev tool", "code editor", "vscode", "vs code",
            "xcode", "editor", "terminal", "developer tools"),
    "comms": ("chat", "messaging", "slack", "discord", "email", "mail", "teams", "messages",
              "comms", "communication"),
    "media": ("media", "video", "videos", "music", "youtube", "netflix", "spotify"),
    "web": ("web", "browser", "browsers", "safari", "chrome", "firefox"),
    "productivity": ("productivity", "notes", "calendar", "docs"),
    "utility": ("utility", "utilities"),
}

# The statement must be about JARVIS AUTO-HIDING (not merely contain the word "hide"), to avoid
# dropping unrelated memories like "I hide my feelings".
_AUTOHIDE_RE = re.compile(
    r"\bauto-?hide\b|\bhide[^.]*\bwhen\b[^.]*\b(distract|fragment|focus)|"
    r"\bjarvis\b[^.]*\bhide\b|\bhide\b[^.]*\b(apps?|tools?)\b",
    re.IGNORECASE,
)
_HABIT_TERM_RE = re.compile(r"^<distracted_hide_(.+?) --> \[approved\]>$")


def _category_of(fact: str) -> str | None:
    """The governed category a fact tries to control via JARVIS auto-hiding, else None. Requires BOTH
    an auto-hide intent AND a known category synonym — precise, to avoid false drops."""
    if not _AUTOHIDE_RE.search(fact):
        return None
    low = fact.lower()
    for category, synonyms in _CATEGORY_SYNONYMS.items():
        if any(re.search(rf"\b{re.escape(s)}\b", low) for s in synonyms):
            return category
    return None


def conflicting_habit(fact: str, beliefs: list[tuple[str, float, float]]) -> tuple[str, bool] | None:
    """If `fact` asserts auto-hide control over a category that has a CONFIDENT sentinel habit, return
    (category, enabled); else None. v1 treats any confident governing habit as authoritative — such a
    statement belongs in the gate, not conversational memory, whether it agrees or contradicts."""
    category = _category_of(fact)
    if category is None:
        return None
    for term, freq, conf in beliefs:
        m = _HABIT_TERM_RE.match(term)
        if m is None or m.group(1) != category:
            continue
        exp = _expectation(freq, conf)
        if exp >= EXP_FLOOR:
            return (category, True)
        if exp <= 1.0 - EXP_FLOOR:
            return (category, False)
    return None  # no confident governing habit -> not a control-plane conflict -> save normally


def grounding_notice(category: str, enabled: bool) -> str:
    """The deterministic reply when a conversational autonomy-control statement hits the gate."""
    friendly = _FRIENDLY.get(category, category)
    state = "enabled" if enabled else "disabled"
    flip = "disable" if enabled else "enable"
    return (f"Auto-hiding {friendly} apps is controlled by your learned settings — it's currently "
            f"{state}. I won't change that from a casual request; approve it when the sentinel next "
            f"offers, to {flip} it.")


# ── ADR-014: OUTPUT grounding — catch self-fact hallucinations in the LLM's answer ──
# slot_id -> human label for the visible correction notice.
_SLOT_LABEL: dict[str, str] = {
    "name": "name", "lives_in": "location", "age": "age", "employer": "employer",
    "editor": "editor", "indentation_pref": "indentation preference", "timezone": "timezone",
}

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")


def ground_answer(answer: str, held: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """Detect a flagrant self-fact contradiction in `answer` against held single-valued facts.

    `held` = `(slot_id, value)` pairs we hold (values already normalized by `slot_of`). Runs `slot_of`
    over each sentence of the answer; if the answer asserts the SAME slot with a DIFFERENT value than a
    held fact, returns `(slot_id, held_value, answer_value)` for the first such conflict, else None.
    Pure, deterministic, no model. Bounded recall: only catches phrasings `slot_of` recognizes (fails
    open — a missed contradiction passes through, same as pre-ADR-014)."""
    if not held:
        return None
    held_by_slot = dict(held)  # one value per single-valued slot
    for sentence in _SENTENCE_SPLIT.split(answer):
        a = slot_of(sentence)
        if a is None:
            continue
        slot_id, answer_value = a
        held_value = held_by_slot.get(slot_id)
        # Containment, not strict inequality: slot_of captures the value greedily ("london these
        # days"), so require that NEITHER value contains the other before flagging — this avoids a
        # false correction on agreement ("Los Angeles these days" ⊇ "los angeles") while still
        # catching genuine divergence ("london …" vs "los angeles"). Fails open by design.
        if held_value is not None and held_value not in answer_value and answer_value not in held_value:
            return (slot_id, held_value, answer_value)
    return None


def correction_notice(slot_id: str, true_value: str) -> str:
    """The visible, deterministic reply that REPLACES a hallucinated answer (the hallucination is
    suppressed, never shown). Transparency: the user sees the grounded truth + that the guard fired."""
    label = _SLOT_LABEL.get(slot_id, slot_id)
    return (f"⚠ Correction: you've told me your {label} is \"{true_value}\" — I'll go with what "
            f"you've taught me, not a guess.")
