"""Slice 2 model boundary: the guarded slow-pass parameter extractor.

The ONLY triage module that calls the LLM (the AST guard allowlists it). Per salient clause it: (1) runs
the parameter grammar N times under temperature jitter and keeps only parameters STABLE across all passes
(reusing consensus.stable_across — fail-closed on flutter); (2) grounds each survivor with verify_gate —
raw_quote must be a verbatim subsequence of the clause (L1) and every numeric token in `value` must appear
in raw_quote (L2); (3) normalizes survivors to Parameters. Reuses, never rebuilds, consensus + verify_gate.
"""
from __future__ import annotations

import json
import os

from language.consensus import _DEFAULT_TEMPS, stable_across
from language.verify_gate import _norm, evidence_grounded
from triage.parameter import Parameter, normalize
from triage.structure import Anchor

_GRAMMAR_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "language", "grammar_parameter.gbnf")

PARAM_PROMPT = (
    "Extract every operative PARAMETER from this contract clause as a JSON array. For each, give: the "
    "verbatim raw_quote it comes from; its role; its numeric value as a string (empty string if the term "
    "is qualitative); its unit; and is_qualitative=true for vague terms like 'promptly', 'without undue "
    "delay', or 'commercially reasonable'. Decompose 'within three (3) business days' as value='3', "
    "unit='business_days'. Do NOT convert units. Assert ONLY what the clause states."
)


def _grammar() -> str:
    with open(_GRAMMAR_PATH, encoding="utf-8") as fh:
        return fh.read()


def _param_key(p: dict) -> tuple:
    return (p.get("role", ""), p.get("unit", ""), str(p.get("value", "")), bool(p.get("is_qualitative")))


def _extract_raw(llm, clause_text: str, temperature: float) -> list[dict]:
    raw = llm.generate_json(PARAM_PROMPT, clause_text, _grammar(), max_tokens=512, temperature=temperature)
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except ValueError:
        return []


def _value_grounded(value: str, raw_quote: str) -> bool:
    """L2 for parameters: the numeric value must appear verbatim in the raw_quote (numeral-aware — unlike
    verify_gate.values_grounded, this does NOT skip short tokens, so '24'/'72'/'3' are checked)."""
    if not value:
        return True                                              # qualitative / empty -> nothing to ground
    return value in {t.rstrip("%") for t in _norm(raw_quote).split()}


def _grounded(p: dict, clause_text: str) -> bool:
    rq = p.get("raw_quote", "")
    if not evidence_grounded(rq, clause_text):                    # L1: raw_quote is verbatim from the clause
        return False
    return _value_grounded(str(p.get("value", "")), rq)          # L2: value traceable to raw_quote


def extract_parameters(llm, clause_text: str, *, clause_type: str, anchor: Anchor,
                       temps: tuple = _DEFAULT_TEMPS) -> list[Parameter]:
    """Slow-pass extraction for one salient clause: consensus -> grounding -> normalize. Returns Parameters."""
    passes = [_extract_raw(llm, clause_text, t) for t in temps]
    stable = stable_across(passes, _param_key)
    return [normalize(p, clause_type=clause_type, anchor=anchor)
            for p in stable if _grounded(p, clause_text)]
