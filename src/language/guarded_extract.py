"""Guarded extractor (v1.24.0 extraction redesign) — Imperative Shell (S-02).

Direct-from-source extraction (Path B) under the GUARDED grammar: every claim carries a mandatory
deontic + a verbatim `evidence` quote, and may use the richer optional shapes (Temporal/Conditional/
Quantitative). The structure is grammar-guaranteed; faithfulness is the separate, deterministic
`verify_gate`'s job. This module only PROPOSES; it never decides what reaches L2.
"""
from __future__ import annotations

import json
import os

_GRAMMAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grammar_guarded.gbnf")

GUARDED_PROMPT = (
    "You are a precise legal-text extractor. Extract the obligations and facts the text states as a JSON "
    "array of claims. Rules:\n"
    "- Choose the RICHEST shape that fits: ConditionalClaim {if, then} for an if/trigger -> consequence; "
    "TemporalClaim {subject, action, object, within_value, within_unit} ONLY for a deadline of the form "
    "'within X <unit>'; QuantitativeClaim {subject, metric, amount} for an amount/cap/rate/threshold; "
    "RelationClaim {subject, verb, object} or PropertyClaim {subject, value} otherwise.\n"
    "- 'deontic' is MANDATORY on every claim: 'shall'/'must' for a duty, 'may' for a right, 'shall_not'/"
    "'must_not' for a prohibition, 'none' for a plain fact.\n"
    "- 'evidence' is MANDATORY: a SINGLE VERBATIM quote copied from the text that contains the subject, the "
    "action/relation, and any value/deadline/condition you assert in that claim. Do not paraphrase it.\n"
    "- Assert ONLY what the text states. Do NOT fill a deadline or condition slot that is not in the text. "
    "Do NOT invent or convert numbers (keep '1.5%' as '1.5%', never 'basis points')."
)


def _grammar_text() -> str:
    with open(_GRAMMAR_PATH, encoding="utf-8") as fh:
        return fh.read()


def extract_guarded(llm, text: str, max_tokens: int = 768, temperature: float = 0.0) -> list[dict]:
    """Run the guarded grammar over `text`. Returns the raw proposed claim dicts (UNVERIFIED).
    `temperature` is raised only by the perturbation-consensus loop to probe binding stability."""
    raw = llm.generate_json(GUARDED_PROMPT, text, _grammar_text(), max_tokens=max_tokens,
                            temperature=temperature)
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except ValueError:
        return []
