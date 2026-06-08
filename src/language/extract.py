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
from typing import Callable

from .ground import cosine_similarity

# Cosine at/above which a candidate fact is treated as a paraphrase of an injected memory line and
# dropped. Tuned below the gate's synonym floor (0.90) to catch person/phrasing restatements
# ("my name is Ashkan" ≈ "the user's name is Ashkan") while staying high enough that two genuinely
# distinct facts about the user are not collapsed.
SEM_ECHO_THRESHOLD = 0.88

# Tolerant by design (a plain-text 7B drifts): case-insensitive, flexible spacing, inline or own
# line. The captured group is the fact text, taken non-greedily up to the closing `]]`.
REMEMBER_TAG = re.compile(r"\[\[\s*REMEMBER\s*:\s*(.+?)\s*\]\]", re.IGNORECASE)
# Mirror of REMEMBER for explicit corrections / "forget that …" (ADR-009) — soft-deletes a memory.
FORGET_TAG = re.compile(r"\[\[\s*FORGET\s*:\s*(.+?)\s*\]\]", re.IGNORECASE)
# Conversational actions (ADR-019): the assistant requests a Mac action it wants to perform. The
# captured item is `<action>` or `<action>: <argument>`; `split_do_directives` splits the two. The
# action name is validated against the CLOSED actions catalog downstream — a bad name is a safe no-op.
DO_TAG = re.compile(r"\[\[\s*DO\s*:\s*(.+?)\s*\]\]", re.IGNORECASE)

MAX_FACTS = 3          # conservative cap per turn — a single utterance rarely teaches more
MAX_FACT_LEN = 200     # a "fact" longer than this is almost certainly the model misusing the tag


def _collect(reply: str, tag: re.Pattern[str]) -> tuple[str, list[str]]:
    """Strip every `tag` directive from `reply`; return (cleaned text, captured items). Shared by the
    REMEMBER and FORGET parsers. Ignores empty/over-long captures; caps at MAX_FACTS; dedups."""
    items: list[str] = []
    for raw in tag.findall(reply):
        item = raw.strip()
        if item and len(item) <= MAX_FACT_LEN and item not in items:
            items.append(item)
        if len(items) >= MAX_FACTS:
            break
    return _strip_tags(reply, tag), items


def split_memory_directives(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (user-facing text, facts to remember). On no `[[REMEMBER]]` tag returns
    `(reply, [])` — today's behavior."""
    return _collect(reply, REMEMBER_TAG)


def split_forget_directives(reply: str) -> tuple[str, list[str]]:
    """Split a reply into (user-facing text, facts to forget) from `[[FORGET: …]]` directives."""
    return _collect(reply, FORGET_TAG)


def split_do_directives(reply: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a reply into (user-facing text, [(action_name, argument)]) from `[[DO: …]]` directives.
    `[[DO: mute]]` -> ("mute", ""); `[[DO: open_url: https://x]]` -> ("open_url", "https://x"). The
    name is lower-cased for catalog lookup; the argument is kept verbatim (its own validator runs
    downstream). On no `[[DO]]` tag returns `(reply, [])`."""
    clean, items = _collect(reply, DO_TAG)
    return clean, [_split_action(item) for item in items]


def _split_action(item: str) -> tuple[str, str]:
    """Split one captured directive into (action_name, argument) on the first ':'."""
    name, _sep, arg = item.partition(":")
    return name.strip().lower(), arg.strip()


def memory_acknowledgment(facts: list[str]) -> str:
    """Build the brief, visible save confirmation appended to the reply (empty if nothing saved)."""
    if not facts:
        return ""
    return "(Saved: " + "; ".join(facts) + ")"


# The acknowledgment is a VISUAL affordance (on-screen). Spoken aloud it breaks the conversational
# illusion, so the voice path strips it. Anchored to the trailing "(Saved: …)"/"(Forgot: …)" line.
_ACK_SUFFIX = re.compile(r"\s*\((?:Saved|Forgot):[^\n]*$")


def strip_acknowledgment(text: str) -> str:
    """Remove a trailing '(Saved: …)'/'(Forgot: …)' confirmation — for the TTS payload, which should
    not voice it. Inverse of the ack builders; leaves text without an acknowledgment untouched."""
    return _ACK_SUFFIX.sub("", text).rstrip()


def filter_known(facts: list[str], known: list[str]) -> list[str]:
    """Drop facts the model merely echoed back from its injected memory (the context-echo bug).

    The HARD guard behind the prompt's soft rule: an LLM handed a 'Persistent memory' block often
    re-tags those facts verbatim, which would re-save what it already knows in an expanding loop.
    Echoes are near-verbatim, so we compare on a normalized form (case / leading article / quotes /
    whitespace / trailing punctuation). This catches verbatim and near-verbatim repeats; it is NOT a
    semantic matcher, so a genuine paraphrase can still slip through (then handled by dedup/visibility).
    """
    seen = {_normalize(k) for k in known if k}
    return [f for f in facts if _normalize(f) not in seen]


def filter_semantic(facts: list[str], known: list[str],
                    embed: Callable[[str], list[float]],
                    threshold: float = SEM_ECHO_THRESHOLD) -> list[str]:
    """Drop facts whose MEANING matches an injected memory line — the paraphrase echoes that
    `filter_known` (verbatim/normalized) misses, e.g. the model restating injected "my name is
    Ashkan" as "the user's name is Ashkan". `embed` maps text -> vector; pure given `embed`, so it
    unit-tests with a fake embedder. The caller supplies a real embedder only when one is wired
    (degrades to the verbatim guard + prompt offline)."""
    if not facts or not known:
        return facts
    known_vecs = [embed(k) for k in known if k]
    if not known_vecs:
        return facts
    kept: list[str] = []
    for f in facts:
        fv = embed(f)
        if all(cosine_similarity(fv, kv) < threshold for kv in known_vecs):
            kept.append(f)
    return kept


def _normalize(s: str) -> str:
    """Canonical form for echo comparison. Conservative on purpose — over-normalizing risks dropping
    genuinely new facts that merely look similar."""
    s = s.strip().strip("\"'“”‘’").lower()
    s = re.sub(r"^(the|a|an)\s+", "", s)   # leading article ("The user's…" == "user's…")
    s = re.sub(r"\s+", " ", s)             # collapse internal whitespace
    return s.strip(" .,!?;:")              # trailing/leading punctuation


def _strip_tags(reply: str, tag: re.Pattern[str]) -> str:
    """Remove all `tag` directives and tidy the whitespace they leave behind. Pure."""
    text = tag.sub("", reply)
    text = re.sub(r"[ \t]{2,}", " ", text)      # collapse gaps left by inline removal
    text = re.sub(r"[ \t]+\n", "\n", text)      # trailing spaces on a line
    text = re.sub(r"\n{3,}", "\n\n", text)      # blank lines left by own-line directives
    return text.strip()
