"""Single-valued-slot registry for memory supersedence (ADR-009). Functional Core (S-02) — pure.

Cosine similarity measures topical proximity but is blind to *mutual exclusivity*: "prefers tabs"
and "prefers spaces" are as close as "likes tea" and "likes coffee", yet only the first pair is a
contradiction. The difference is the predicate's cardinality — a *single-valued* slot (name, where
you live, your editor) holds exactly one value, so a new value supersedes the old; a multi-valued
predicate (likes, knows) just accumulates.

This module is the deterministic decider that runs (zero LLM calls) on the few candidates cosine
narrows down. It is a CLOSED, human-authored registry — same default-deny posture as
`execution.catalog`: only precise, clearly single-valued predicates are listed, and anything that
doesn't match returns None → **keep both**. Under-matching (a missed supersede the user can redo)
is always safer than over-matching (a false supersede); soft tombstones make even that reversible.
"""
from __future__ import annotations

import re

# slot_id -> patterns, each with ONE capture group = the slot's value. Add precise predicates here;
# a vague one (e.g. "is a <role>", which is multi-valued) is deliberately omitted.
_SLOT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "name": (
        re.compile(r"\bname is ([\w'-]+)", re.I),
        re.compile(r"\b(?:is called|goes by) ([\w'-]+)", re.I),
    ),
    "lives_in": (
        re.compile(r"\b(?:lives?|resides?|based|located) in ([A-Za-z][\w .'-]*)", re.I),
    ),
    "indentation_pref": (
        re.compile(r"\bprefers? (tabs|spaces)\b", re.I),
    ),
    "editor": (
        re.compile(r"\beditor is ([\w +]+)", re.I),
        re.compile(r"\buses (vim|neovim|emacs|vscode|vs code|nano|sublime|pycharm|intellij|xcode)\b", re.I),
    ),
    "employer": (
        re.compile(r"\b(?:works at|works for|employed by|employer is) ([A-Za-z][\w .&'-]*)", re.I),
    ),
    "age": (
        re.compile(r"\b(?:is )?(\d{1,3}) years? old", re.I),
        re.compile(r"\bage is (\d{1,3})\b", re.I),
    ),
    "timezone": (
        re.compile(r"\btimezone is ([\w/+-]+)", re.I),
        re.compile(r"\bin the ([\w/+-]+) timezone", re.I),
    ),
}


def _norm(s: str) -> str:
    """Canonical value form for comparison (case / whitespace / surrounding punctuation)."""
    return re.sub(r"\s+", " ", s.strip().lower()).strip(" .,!?;:")


def slot_of(text: str) -> tuple[str, str] | None:
    """Return (slot_id, normalized_value) if `text` fills a single-valued slot, else None.
    First registered match wins; None means 'no known single-valued slot' → callers keep both."""
    for slot_id, patterns in _SLOT_PATTERNS.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                return slot_id, _norm(m.group(1))
    return None


def same_single_valued_slot(a: str, b: str) -> bool:
    """True iff `a` and `b` fill the SAME single-valued slot with DIFFERENT values — i.e. a genuine
    contradiction where the newer should supersede the older. Same slot + same value is not a
    contradiction (it's a restatement); no slot on either side is not a contradiction (keep both)."""
    sa, sb = slot_of(a), slot_of(b)
    return sa is not None and sb is not None and sa[0] == sb[0] and sa[1] != sb[1]
