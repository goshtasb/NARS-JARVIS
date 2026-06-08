"""Conversational-memory directive extraction (ADR-008). Functional Core (S-02) — pure, model-free.

The LLM-first brain (ADR-007) answers in free text. To let it persist things it learns mid-
conversation WITHOUT a separate model call, the assistant is prompted to embed a machine-readable
directive in its reply for each memorable item:

    [[REMEMBER: <concise third-person fact>]]

This module is the SINGLE SOURCE OF TRUTH for that directive's syntax. `Jarvis.converse` calls
`split_memory_directives` to pull the facts out and strip the tags from what the user sees, then
`memory_acknowledgment` to build the visible "(Saved: …)" confirmation. Everything here is pure
string processing — no model, no I/O — so it is fully unit-testable and degrades safely: a reply
with no (or malformed) directive simply yields the reply unchanged and no facts.
"""
from __future__ import annotations

import re

# Tolerant by design (a plain-text 7B drifts): case-insensitive, flexible spacing, inline or own
# line. The captured group is the fact text, taken non-greedily up to the closing `]]`.
REMEMBER_TAG = re.compile(r"\[\[\s*REMEMBER\s*:\s*(.+?)\s*\]\]", re.IGNORECASE)

MAX_FACTS = 3          # conservative cap per turn — a single utterance rarely teaches more
MAX_FACT_LEN = 200     # a "fact" longer than this is almost certainly the model misusing the tag


def split_memory_directives(reply: str) -> tuple[str, list[str]]:
    """Split an assistant reply into (user-facing text, extracted facts).

    Strips every `[[REMEMBER: …]]` directive from `reply` and returns the cleaned prose plus the
    list of facts to persist. Empty/whitespace-only and over-long captures are ignored; the result
    is capped at `MAX_FACTS`. On no match this returns `(reply, [])` — i.e. today's behavior.
    """
    facts: list[str] = []
    for raw in REMEMBER_TAG.findall(reply):
        fact = raw.strip()
        if fact and len(fact) <= MAX_FACT_LEN and fact not in facts:
            facts.append(fact)
        if len(facts) >= MAX_FACTS:
            break
    return _strip_tags(reply), facts


def memory_acknowledgment(facts: list[str]) -> str:
    """Build the brief, visible save confirmation appended to the reply (empty if nothing saved)."""
    if not facts:
        return ""
    return "(Saved: " + "; ".join(facts) + ")"


def _strip_tags(reply: str) -> str:
    """Remove all directives and tidy the whitespace they leave behind. Pure."""
    text = REMEMBER_TAG.sub("", reply)
    text = re.sub(r"[ \t]{2,}", " ", text)      # collapse gaps left by inline removal
    text = re.sub(r"[ \t]+\n", "\n", text)      # trailing spaces on a line
    text = re.sub(r"\n{3,}", "\n\n", text)      # blank lines left by own-line directives
    return text.strip()
