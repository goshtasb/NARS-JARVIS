"""Deterministic verification gate for guarded extraction (v1.24.0 extraction redesign) — Functional Core (S-02).

The proposer (the LLM) emits structured claims; THIS module is the grader — and the grader is the SOURCE
TEXT plus fixed, auditable lexicons, NEVER the model. Four layers; each fails a binding CLOSED (stripped or
rejected, never guessed) rather than admit an unverifiable assertion into the L2 vault:

  L1 provenance : claim['evidence'] must be a verbatim token-subsequence of the source chunk. The model can
                  fabricate a claim, but not its citation — a quote that isn't in the source is rejected.
  L2 values     : every content/numeric token in the claim's semantic fields must be grounded in the
                  (L1-verified) evidence — morphology-aware for words, EXACT for numerals. Kills the
                  "1.5% -> 1500 basis points" line-item fabrication.
  L3 cue-role   : a TemporalClaim (a deadline) is admitted only if a DEADLINE cue governs the number in the
                  evidence and no RATE/DURATION/WINDOW cue does; a ConditionalClaim needs a conditional cue.
                  Kills the look-back-window-as-deadline mis-binding. Failing role -> the slot is STRIPPED to
                  its verified relational core (fail-closed), not dropped silently.
  L4 sanity     : closed-set checks — within_unit in the unit set, within_value numeric, deontic in its set.

Pure + deterministic: no I/O, no model, no clock. Fully unit-testable without llama.cpp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── closed-class lexicons (auditable; the semantic weight rests HERE, not on the model) ──
_DEADLINE = ("within", "no later than", "not later than", "by no later", "before")
_DURATION = ("for a period of", "for")
_WINDOW = ("preceding", "prior", "in the past", "following", "during")
_RATE = ("per", "each", "every", "once per")
_UNITS = frozenset(("second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
                    "business day", "business days", "week", "weeks", "month", "months", "year", "years"))
_DEONTIC = frozenset(("shall", "may", "shall_not", "must", "must_not", "none"))
_STOP = frozenset(("the", "a", "an", "of", "to", "and", "or", "in", "on", "at", "by", "for", "with",
                   "any", "all", "its", "such", "that", "this", "be", "is", "are", "from", "than"))
_NUMWORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fourteen": "14",
    "thirty": "30", "ninety": "90", "seventy-two": "72", "seventy two": "72",
}
_SEMANTIC_FIELDS = ("subject", "verb", "object", "value", "action", "if", "then", "metric", "amount",
                    "within_value", "within_unit")


def _norm(s: str) -> str:
    """Lowercase; keep alphanumerics, '%', and a '.' ONLY as a decimal point (so '1.5' survives but the
    sentence-final '.' in 'claim.' does not stick to the token); collapse everything else to spaces."""
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", (s or "").lower())   # drop non-decimal periods
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.%]+", " ", s)).strip()


def _has_cue(ev_norm: str, cues) -> bool:
    """A cue matches only as a whole word (so 'per' does not fire inside 'period', 'for' inside 'before')."""
    return any(re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", ev_norm) for c in cues)


def _toks(s: str) -> list[str]:
    return _norm(s).split()


# ── L1: provenance — evidence is a verbatim token-subsequence of the source ──
def evidence_grounded(evidence: str, source: str) -> bool:
    ev, src = _toks(evidence), _toks(source)
    if not ev:
        return False
    n = len(ev)
    return any(src[i:i + n] == ev for i in range(0, len(src) - n + 1))


# ── L2: value grounding — every content/numeric token traceable to the evidence ──
def _is_num(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?%?", tok))


def _num_grounded(tok: str, ev_toks: list[str]) -> bool:
    bare = tok.rstrip("%")
    return any(bare == e.rstrip("%") for e in ev_toks)         # EXACT for numerals (no 1.5 -> 1500)


def _word_grounded(tok: str, ev_toks: list[str]) -> bool:
    if tok in ev_toks:
        return True
    if len(tok) >= 4:                                          # morphology: notify/notification, liable/liability
        pre = tok[:4]
        return any(e.startswith(pre) for e in ev_toks if len(e) >= 4)
    return False


def values_grounded(claim: dict, evidence: str) -> list[str]:
    """Return the list of UNGROUNDED content/numeric tokens (empty list == fully grounded)."""
    ev_toks = _toks(evidence)
    missing: list[str] = []
    for f in _SEMANTIC_FIELDS:
        v = claim.get(f)
        if not v:
            continue
        for tok in _toks(str(v).replace("_", " ")):
            if tok in _STOP or len(tok) < 3:
                continue
            ok = _num_grounded(tok, ev_toks) if _is_num(tok) else (
                _word_grounded(tok, ev_toks) or _num_grounded(_NUMWORDS.get(tok, tok), ev_toks))
            if not ok and tok not in missing:
                missing.append(tok)
    return missing


# ── L3: cue-role — which closed-class cue governs the number/condition in the evidence ──
def cue_role(evidence: str, value: str = "") -> str:
    ev = _norm(evidence)
    if _has_cue(ev, _RATE):
        return "rate"
    if _has_cue(ev, _DEADLINE):
        return "deadline"
    if _has_cue(ev, _DURATION):
        return "duration"
    if _has_cue(ev, _WINDOW):
        return "window"
    return "uncued"


_COND_CUES = ("if", "unless", "provided", "in the event", "should", "upon", "where", "when")


def conditional_cued(evidence: str) -> bool:
    return _has_cue(_norm(evidence), _COND_CUES)


# ── L4: closed-set sanity ──
def sanity_ok(claim: dict) -> list[str]:
    problems: list[str] = []
    if claim.get("deontic", "none") not in _DEONTIC:
        problems.append("bad_deontic")
    if claim.get("type") == "TemporalClaim":
        if _norm(claim.get("within_unit", "")) not in _UNITS:
            problems.append("bad_unit")
        if not re.fullmatch(r"\d+(\.\d+)?", _norm(claim.get("within_value", ""))):
            problems.append("nonnumeric_value")
    return problems


@dataclass
class GateResult:
    admit: bool
    kept: dict | None                 # the claim to write (possibly DEGRADED), or None if rejected
    reasons: list[str] = field(default_factory=list)
    degraded: bool = False


def _degrade_temporal(claim: dict) -> dict:
    """Strip the unverifiable deadline slot, keep the verified relational core."""
    return {"type": "RelationClaim", "deontic": claim.get("deontic", "none"),
            "subject": claim.get("subject", ""), "verb": claim.get("action", ""),
            "object": claim.get("object", ""), "evidence": claim.get("evidence", "")}


def verify(claim: dict, source: str) -> GateResult:
    """Run the four layers. Returns admit/kept/reasons. The model is never consulted — only the source."""
    ev = claim.get("evidence", "")
    if not evidence_grounded(ev, source):                                  # L1
        return GateResult(False, None, ["L1:evidence_not_in_source"])
    missing = values_grounded(claim, ev)                                   # L2
    if missing:
        return GateResult(False, None, [f"L2:ungrounded_values={missing}"])
    problems = sanity_ok(claim)                                            # L4
    ctype = claim.get("type")
    if ctype == "TemporalClaim":
        role = cue_role(ev, claim.get("within_value", ""))                 # L3 (deadline)
        if "bad_unit" in problems or "nonnumeric_value" in problems or role != "deadline":
            kept = _degrade_temporal(claim)
            return GateResult(True, kept, [f"L3:temporal_stripped(role={role};{problems})"], degraded=True)
    if ctype == "ConditionalClaim" and not conditional_cued(ev):           # L3 (conditional)
        return GateResult(False, None, ["L3:conditional_without_cue"])
    if "bad_deontic" in problems:
        return GateResult(False, None, ["L4:bad_deontic"])
    return GateResult(True, claim, ["ok"])
