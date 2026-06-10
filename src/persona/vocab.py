"""The CLOSED persona vocabulary (ADR-036) — Functional Core (S-02).

The single, developer-curated source of truth for what the persona layer may learn and inject. The
7B extractor may ONLY emit these (predicate, value) pairs; anything else is dropped. This closed list
is also the prompt-injection bound: untrusted scraped text can at most nudge a *known* dimension, never
invent an arbitrary instruction. Expanding it is a human code change here — never runtime/LLM-driven.

Each (predicate, value) maps to: the ONA term it becomes, and the one-line English constraint that gets
prepended to the LLM system prompt when its confidence clears the floor.
"""
from __future__ import annotations

# (predicate, value) -> human constraint string injected into the prompt.
VOCAB: dict[tuple[str, str], str] = {
    ("format_directive", "terse_markdown_tables"): "Structure output as concise markdown tables.",
    ("format_directive", "omit_greeting_prose"):   "Omit greetings and filler prose; answer directly.",
    ("format_directive", "cite_sources_explicitly"): "Cite sources explicitly.",
    ("current_focus", "local_development"):         "The user is currently focused on local development.",
    ("current_focus", "unverified_data_synthesis"):
        "The user is synthesizing unverified data — flag uncertainty and do not present it as fact.",
}

_PREDICATES = {p for (p, _v) in VOCAB}


def is_known(predicate: str, value: str) -> bool:
    """True iff this exact (predicate, value) pair is in the closed vocabulary."""
    return (predicate, value) in VOCAB


def predicates() -> list[str]:
    return sorted(_PREDICATES)


def term(predicate: str, value: str) -> str:
    """The ONA inheritance term for a vocabulary pair, e.g. '<format_directive --> omit_greeting_prose>'."""
    return f"<{predicate} --> {value}>"


def split_term(term_str: str) -> tuple[str, str] | None:
    """Parse a '<predicate --> value>' term back into (predicate, value), or None if it isn't one."""
    t = (term_str or "").strip()
    if not (t.startswith("<") and t.endswith(">") and " --> " in t):
        return None
    pred, val = t[1:-1].split(" --> ", 1)
    return pred.strip(), val.strip()


def phrase_for(term_str: str) -> str | None:
    """The injectable English constraint for a term, or None if the term is outside the vocabulary."""
    pair = split_term(term_str)
    return VOCAB.get(pair) if pair else None


def catalog_for_prompt() -> str:
    """A compact listing of the allowed (predicate, value) pairs, shown to the extractor LLM."""
    return "\n".join(f"- {p} / {v}" for (p, v) in VOCAB)


def render_persona(rows: list[dict]) -> str:
    """The `[COGNITIVE CONTEXT CONSTRAINTS]` block prepended to the LLM system prompt, built from the
    confident persona rows (`[{term, frequency, confidence}]`). Only in-vocabulary terms become text;
    '' when there's nothing to inject. Lives here (not in `context/`) because it owns the vocabulary —
    a module contains everything it needs (S-01)."""
    phrases = [p for r in rows if (p := phrase_for(r.get("term", "")))]
    if not phrases:
        return ""
    return "[COGNITIVE CONTEXT CONSTRAINTS]\n" + "\n".join(f"- {p}" for p in phrases)
